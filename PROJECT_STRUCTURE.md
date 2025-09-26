# Project Structure

```
arrl-10ghz-web/
├── README.md                           # Main documentation
├── PROJECT_STRUCTURE.md               # This file
├── package.json                       # Node.js dependencies and scripts
├── tsconfig.json                      # TypeScript configuration
├── cdk.json                          # CDK configuration
├── .gitignore                        # Git ignore patterns
├── deploy.sh                         # Automated deployment script
├── build-layer.sh                    # Python layer build script
├── verify-deployment.sh              # Deployment verification script
│
├── frontend/                         # Static web application
│   ├── index.html                   # Main HTML page
│   ├── styles.css                   # CSS styling (matches reference design)
│   └── script.js                    # JavaScript functionality
│
├── infrastructure/                   # AWS CDK infrastructure code
│   ├── app.ts                      # CDK app entry point
│   └── arrl-10ghz-web-stack.ts     # Main stack definition
│
└── lambda/                          # AWS Lambda functions
    ├── functions/
    │   └── process/                 # Contest log processing function
    │       ├── process.py          # Main Lambda handler
    │       └── requirements.txt    # Python dependencies
    └── layers/
        └── python/                  # Shared Python dependencies layer
            └── requirements.txt     # Layer dependencies
```

## Key Components

### Frontend (`frontend/`)
- **index.html**: Single-page application with form for contest log processing
- **styles.css**: Professional styling matching amateur radio contest theme
- **script.js**: Client-side logic for form handling, file uploads, and API communication

### Infrastructure (`infrastructure/`)
- **app.ts**: CDK application entry point, sets up the stack
- **arrl-10ghz-web-stack.ts**: Defines all AWS resources:
  - S3 buckets for website hosting and file storage
  - Lambda function for processing
  - API Gateway for REST endpoints
  - CloudFront distribution for global delivery

### Lambda Functions (`lambda/`)
- **process/process.py**: Main processing logic that:
  - Handles file uploads and Google Sheets URLs
  - Converts contest logs to Cabrillo format
  - Generates analysis reports and visualizations
  - Uploads results to S3 and returns download URLs
- **layers/python/**: Shared dependencies (pandas, matplotlib, etc.)

## Deployment Flow

1. **Build Phase**: `build-layer.sh` installs Python dependencies
2. **CDK Deploy**: Infrastructure is created/updated
3. **Frontend Deploy**: Static files are uploaded to S3
4. **Verification**: `verify-deployment.sh` tests the deployment

## AWS Resources Created

- **S3 Buckets**:
  - Website hosting bucket (public read)
  - Files bucket (temporary storage with 24h lifecycle)
- **Lambda Function**: Python 3.11 runtime with 5-minute timeout
- **API Gateway**: REST API with CORS enabled
- **CloudFront**: Global CDN with custom behaviors for API routing
- **IAM Roles**: Least-privilege access for Lambda

## Development Workflow

1. **Local Development**: Use `npm run dev` for frontend testing
2. **Infrastructure Changes**: Modify CDK code and redeploy
3. **Lambda Changes**: Update function code and redeploy
4. **Testing**: Use `verify-deployment.sh` to confirm functionality