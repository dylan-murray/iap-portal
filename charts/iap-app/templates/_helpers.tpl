{{- define "iap-app.namespace" -}}
iap-app-{{ .Values.slug }}
{{- end -}}

{{- define "iap-app.displayName" -}}
{{ default .Values.slug .Values.displayName }}
{{- end -}}

{{- define "iap-app.fqdn" -}}
{{ .Values.slug }}.{{ .Values.domain }}
{{- end -}}

{{- define "iap-app.upstreamService" -}}
{{ .Values.slug }}.{{ include "iap-app.namespace" . }}.svc.cluster.local
{{- end -}}

{{- define "iap-app.issuer" -}}
{{ default (printf "https://portal.%s" .Values.domain) .Values.portal.issuer }}
{{- end -}}

{{- define "iap-app.labels" -}}
app.kubernetes.io/name: {{ .Values.slug }}
app.kubernetes.io/managed-by: iap-portal
iap-apps/slug: {{ .Values.slug }}
{{- end -}}

{{- define "iap-app.validate" -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{0,53}[a-z0-9]$" (.Values.slug | toString)) -}}
{{- fail "slug must match ^[a-z][a-z0-9-]{0,53}[a-z0-9]$" -}}
{{- end -}}
{{- if eq .Values.slug "portal" -}}
{{- fail "slug 'portal' is reserved" -}}
{{- end -}}
{{- if hasKey .Values.env "IAP_PORTAL_DEV" -}}
{{- fail "IAP_PORTAL_DEV fakes identity and must not be deployed" -}}
{{- end -}}
{{- end -}}
