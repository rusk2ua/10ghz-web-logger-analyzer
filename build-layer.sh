#!/bin/bash

# Build Python dependencies layer for Lambda (x86_64 architecture)
echo "Building Python dependencies layer for x86_64..."

# Clean previous build
rm -rf lambda/layers/python/python

# Create layer directory structure
mkdir -p lambda/layers/python/python

# Check if Docker is available
if command -v docker &> /dev/null; then
    echo "Using Docker to build for x86_64 Linux..."
    
    # Use Docker to build dependencies for the correct architecture
    docker run --rm --platform linux/amd64 \
        -v "$(pwd)/lambda/layers/python:/var/task" \
        -w /var/task \
        public.ecr.aws/lambda/python:3.11 \
        pip install -r requirements.txt -t python/
    
    echo "Docker build complete!"
else
    echo "Docker not available. Installing locally (may not work on Lambda if architecture differs)..."
    
    # Fallback to local installation with platform specification
    pip3 install \
        --platform linux_x86_64 \
        --target lambda/layers/python/python \
        --implementation cp \
        --python-version 3.11 \
        --only-binary=:all: \
        --upgrade \
        -r lambda/layers/python/requirements.txt
    
    echo "Local build complete (x86_64 targeted)!"
fi

echo "Layer build complete!"