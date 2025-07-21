# --- Stage 1: Dependency Installation ---
# This stage installs all dependencies (dev and production)
# and is optimized for Docker layer caching.
FROM node:18-alpine AS deps
WORKDIR /app

# Copy package.json and package-lock.json (or yarn.lock) first.
# This step is cached as long as your dependency files don't change.
COPY package*.json ./
# If you use yarn, uncomment the line below and comment out npm install
# COPY yarn.lock ./
# RUN yarn install --frozen-lockfile
RUN npm install

# --- Stage 2: Build the Next.js application ---
# This stage uses the installed dependencies to build the app.
FROM node:18-alpine AS builder
WORKDIR /app

# Copy the node_modules from the 'deps' stage
# This leverages the caching of the previous stage
COPY --from=deps /app/node_modules ./node_modules
# Copy the rest of your application source code
COPY . .

# Generate the Next.js production build artifacts.
# This command creates the crucial .next/ directory.
RUN npm run build

# --- Stage 3: Run the Next.js application in production ---
# This is the final, minimal image containing only what's needed to run.
FROM node:18-alpine AS runner
WORKDIR /app

# Set environment variables for production and host/port
ENV NODE_ENV production
ENV HOST 0.0.0.0
ENV PORT 3000

# Copy *only* the production dependencies from the 'deps' stage.
# This keeps node_modules minimal for runtime.
COPY --from=deps /app/node_modules ./node_modules
# Copy the built Next.js application artifacts from the 'builder' stage.
# The .next/ directory contains the compiled server and client code.
COPY --from=builder /app/.next ./.next
# Copy static assets (e.g., images, fonts) that are served directly.
COPY --from=builder /app/public ./public
# Copy package.json: often needed by 'npm start' or for runtime scripts.
COPY package*.json ./

# Expose the default port for the Next.js server
EXPOSE 3000

# Command to start the Next.js production server.
# 'npm start' typically maps to 'next start' in your package.json scripts.
CMD ["npm", "start"]