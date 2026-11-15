FROM python:3.12-slim
WORKDIR /app
COPY automation.py server.py index.html app.js style.css finance_dashboard.py dashboard.html dashboard.js dashboard.css ./
USER 10001:10001
EXPOSE 8000
CMD ["python", "-B", "server.py"]
