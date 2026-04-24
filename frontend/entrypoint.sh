#!/bin/sh
set -e
cat > /usr/share/nginx/html/config.js <<EOC
window.__API_URL__ = "${FRONTEND_API_URL:-/api}";
window.__CASE_NUMBER_FIELDS__ = "${CASE_NUMBER_FIELDS:-UF_CRM_1708426613594,UF_CRM_CASE_NUMBER,UF_CRM_1699999999,CASE_NUMBER}".split(",").map((v) => v.trim()).filter(Boolean);
EOC
exec nginx -g 'daemon off;'
