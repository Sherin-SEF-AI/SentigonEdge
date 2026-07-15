{{/* Common labels applied to every object. Call with the root context ($). */}}
{{- define "sentigon.labels" -}}
app.kubernetes.io/part-of: sentigon
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Shared env from the ConfigMap + Secret, injected into every app container. */}}
{{- define "sentigon.envFrom" -}}
- configMapRef:
    name: {{ .Release.Name }}-env
- secretRef:
    name: {{ .Release.Name }}-secrets
{{- end -}}
