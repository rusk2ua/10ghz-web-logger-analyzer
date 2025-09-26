#!/bin/bash

echo "Verifying ARRL 10 GHz Web Interface Deployment..."

# Get the CloudFront URL from CDK outputs
WEBSITE_URL=$(aws cloudformation describe-stacks \
    --stack-name Arrl10GhzWebStack \
    --region us-east-2 \
    --query 'Stacks[0].Outputs[?OutputKey==`WebsiteURL`].OutputValue' \
    --output text 2>/dev/null)

API_URL=$(aws cloudformation describe-stacks \
    --stack-name Arrl10GhzWebStack \
    --region us-east-2 \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiURL`].OutputValue' \
    --output text 2>/dev/null)

if [ -z "$WEBSITE_URL" ] || [ -z "$API_URL" ]; then
    echo "❌ Could not retrieve deployment URLs. Stack may not be deployed."
    echo "Run './deploy.sh' to deploy the application."
    exit 1
fi

echo "✅ Deployment found!"
echo "🌐 Website URL: $WEBSITE_URL"
echo "🔗 API URL: $API_URL"

# Test website accessibility
echo ""
echo "Testing website accessibility..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$WEBSITE_URL")

if [ "$HTTP_STATUS" = "200" ]; then
    echo "✅ Website is accessible (HTTP $HTTP_STATUS)"
else
    echo "⚠️  Website returned HTTP $HTTP_STATUS (may still be deploying)"
fi

# Test API endpoint
echo ""
echo "Testing API endpoint..."
API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL}process" -X OPTIONS)

if [ "$API_STATUS" = "200" ]; then
    echo "✅ API is accessible (HTTP $API_STATUS)"
else
    echo "⚠️  API returned HTTP $API_STATUS (may still be deploying)"
fi

echo ""
echo "🎉 Deployment verification complete!"
echo "📖 Open $WEBSITE_URL in your browser to use the application."