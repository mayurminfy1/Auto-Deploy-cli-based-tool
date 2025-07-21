# Stage 1: Build the Vite app
FROM node:18-alpine AS build
WORKDIR /app

# Copy dependencies and install them
COPY package*.json ./
RUN npm install

# Copy source code and build
COPY . .
RUN npm run build

# Stage 2: Serve with a production server
FROM node:18-alpine AS production
WORKDIR /app

# Install lightweight static file server
RUN npm install -g serve

# Copy built files from previous stage
COPY --from=build /app/dist ./dist

# Expose the port your app will run on
EXPOSE 3000

# Start the static server on port 3000
CMD ["serve", "-s", "dist", "-l", "3000"]
