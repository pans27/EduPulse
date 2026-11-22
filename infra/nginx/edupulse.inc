# Shared by default.conf (local, HTTP) and https.conf (Swarm, HTTPS): everything a client-facing
# server block needs to proxy the dashboard, REST API and WebSockets to the API replicas.

server_tokens off;
client_max_body_size 1m;

# Docker's embedded DNS. Resolving "api" per request, rather than once at startup, lets Nginx
# start before the API and follow it when a container is recreated with a new IP.
resolver 127.0.0.11 valid=10s ipv6=off;
set $api http://api:8000;

gzip on;
gzip_types text/css text/javascript application/javascript application/json;

proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_connect_timeout 5s;
proxy_read_timeout    30s;

# Live course updates: keep the connection open far longer than a normal request.
location ~ ^/api/courses/[^/]+/ws$ {
    proxy_pass $api;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
}

location = / {
    return 302 /dashboard/;
}

location / {
    proxy_pass $api;
}
