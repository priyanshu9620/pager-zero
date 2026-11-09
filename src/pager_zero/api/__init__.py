"""FastAPI surface (notes Phase 6). Same lifecycle as the CLI, over HTTP:

POST /incidents                 start an incident (agent runs in the background)
GET  /incidents                 list
GET  /incidents/{id}            meta + diagnosis / plan / verification
GET  /incidents/{id}/trace      audit trail (JSONL as a list)
POST /incidents/{id}/approve    resume → execute → verify (background)
POST /incidents/{id}/reject
POST /webhooks/alertmanager     Alertmanager-compatible ingress
GET  /healthz
"""
