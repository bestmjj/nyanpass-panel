FROM python:3.9-slim

# Create non-root user
#RUN useradd --create-home --shell /bin/bash nyanpass

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY src/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Change ownership to non-root user
#RUN chown -R nyanpass:nyanpass /app

# Switch to non-root user
#USER nyanpass

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Run application
CMD ["python", "src/main.py"]