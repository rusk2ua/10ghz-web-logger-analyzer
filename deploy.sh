#!/bin/bash

echo "Deploying ARRL 10 GHz Contest Logger Web Interface..."

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "Error: AWS CLI not configured. Please run 'aws configure' first."
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
npm install

# Build Python layer
echo "Building Python dependencies layer..."
./build-layer.sh

# Upload frontend files to be included in deployment
echo "Preparing frontend files..."
mkdir -p cdk.out/frontend
cp -r frontend/* cdk.out/frontend/

# Deploy with CDK
echo "Deploying infrastructure..."
npx cdk bootstrap --region us-east-2
npx cdk deploy --require-approval never

echo "Deployment complete!"
echo "Check the CloudFormation outputs for your website URL."