FROM python:3.11

# Install Node.js (>= 18) and necessary tools
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm \
  && rm -rf /var/lib/apt/lists/*

# Copy uv from the official uv image
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Copy dependency descriptor files first to leverage Docker cache
COPY package.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# Install dependencies (Node + Python)
RUN npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# Copy project source code
COPY . .

# Ensure the static directory exists before the build so Flask can start
# even if the frontend build step is skipped or fails partway through.
RUN mkdir -p backend/static

# Build the frontend and copy the dist into backend/static
RUN npm run build

EXPOSE 5001

# Serve everything through Flask
CMD ["sh", "-c", "cd backend && uv run python run.py"]
