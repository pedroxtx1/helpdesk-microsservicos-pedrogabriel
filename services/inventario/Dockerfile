FROM python:3.12-slim
RUN useradd --create-home --uid 10001 helpdesk && mkdir /dados && chown helpdesk:helpdesk /dados
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/__init__.py ./services/__init__.py
COPY services/inventario ./services/inventario
USER helpdesk
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "services.inventario.main:app", "--host", "0.0.0.0", "--port", "8000"]
