#!/bin/sh
set -e
cat > /usr/share/nginx/html/config.js <<EOC
window.__API_URL__ = "${FRONTEND_API_URL:-/api}";
EOC
exec nginx -g 'daemon off;'
