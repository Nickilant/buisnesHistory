#!/bin/sh
set -e
cat > /usr/share/nginx/html/config.js <<EOC
window.__API_URL__ = "${FRONTEND_API_URL:-http://localhost:8000}";
EOC
exec nginx -g 'daemon off;'
