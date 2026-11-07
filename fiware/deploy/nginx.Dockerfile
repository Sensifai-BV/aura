FROM nginx:1.27.3-alpine
COPY wirecloud/nginx/nginx.prod.conf /etc/nginx/conf.d/default.conf
