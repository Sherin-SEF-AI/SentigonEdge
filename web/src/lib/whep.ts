// Minimal WHEP client for MediaMTX low-latency WebRTC playback.
// POST the SDP offer to the whep url, apply the SDP answer. Non-trickle: wait for
// ICE gathering to complete before sending, which works cleanly on a single host.

export interface WhepSession {
  pc: RTCPeerConnection;
  close: () => Promise<void>;
}

function waitIceComplete(pc: RTCPeerConnection, timeoutMs = 3000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => {
      if (pc.iceGatheringState === "complete") done();
    };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, timeoutMs);
  });
}

export async function playWhep(
  whepUrl: string,
  video: HTMLVideoElement,
  onState?: (s: RTCPeerConnectionState) => void,
): Promise<WhepSession> {
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });

  const stream = new MediaStream();
  pc.ontrack = (ev) => {
    stream.addTrack(ev.track);
    video.srcObject = stream;
  };
  if (onState) pc.onconnectionstatechange = () => onState(pc.connectionState);

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitIceComplete(pc);

  const res = await fetch(whepUrl, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription?.sdp ?? offer.sdp ?? "",
  });
  if (!res.ok) {
    pc.close();
    throw new Error(`WHEP ${res.status} ${res.statusText}`);
  }
  const resourceUrl = res.headers.get("location");
  const answer = await res.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answer });

  const close = async () => {
    try {
      if (resourceUrl) {
        const abs = new URL(resourceUrl, whepUrl).toString();
        await fetch(abs, { method: "DELETE" }).catch(() => {});
      }
    } finally {
      pc.close();
    }
  };
  return { pc, close };
}
