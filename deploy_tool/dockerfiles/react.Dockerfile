# Stage 1: Build the Create React App
FROM node:18-alpine AS build
WORKDIR /app

# Copy package.json and package-lock.json (or yarn.lock)
# to leverage Docker layer caching for dependencies
COPY package*.json ./
# If you use yarn, use: COPY yarn.lock ./
RUN npm install
# If you use yarn, use: RUN yarn install --frozen-lockfile

# Copy the rest of the application source code
COPY . .

# Build the Create React App for production
# CRA typically outputs to a 'build' folder
RUN npm run build

# Stage 2: Serve the built application with a lightweight static file server
FROM node:18-alpine AS production
WORKDIR /app

# Install a lightweight static file server globally
# 'serve' is a good choice for serving single-page applications
RUN npm install -g serve

# Copy the built application files from the 'build' stage
# CRA's default output directory is 'build'
COPY --from=build /app/build ./build

# Expose the port your application will run on
# This should match the port configured in your 'serve' command
EXPOSE 3000

# Command to start the static server
# It serves the content from the 'build' directory on port 3000
CMD ["serve", "-s", "build", "-l", "3000"]