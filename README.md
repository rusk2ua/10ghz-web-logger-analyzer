# ARRL 10 GHz Contest Logger - Web Interface

Serverless web frontend for the ARRL 10 GHz and Up Contest logging tools.

## Features

- **File Upload**: Upload multiple log files (.log, .csv, .txt formats)
- **Google Sheets Integration**: Process contest logs directly from shared Google Sheets
- **Multiple Output Formats**: 
  - Cabrillo format log files
  - Contest summaries and scoring breakdowns
  - Station-by-station activity reports
  - Weekend-by-weekend analysis
  - Comprehensive contest analysis
  - Directional visualizations and propagation analysis
  - Multi-log comparison reports
- **Professional UI**: Clean, amateur radio contest-themed interface
- **Serverless Architecture**: Scalable, cost-effective AWS deployment

## Architecture

- **Frontend**: Static React-like web app hosted on S3/CloudFront
- **Backend**: AWS Lambda functions for contest log processing
- **Storage**: S3 for temporary file storage with automatic cleanup
- **API**: API Gateway for REST endpoints
- **Infrastructure**: AWS CDK (TypeScript) for deployment
- **Region**: us-east-2 (Ohio)

## Prerequisites

- Node.js 18+ and npm
- AWS CLI configured with appropriate permissions
- Python 3.11+ (for local development)
- AWS CDK CLI: `npm install -g aws-cdk`

## Quick Start

1. **Clone and setup**:
   ```bash
   git clone <repository-url>
   cd arrl-10ghz-web
   npm install
   ```

2. **Configure AWS credentials**:
   ```bash
   aws configure
   # Enter your AWS Access Key ID, Secret Access Key, and set region to us-east-2
   ```

3. **Deploy to AWS**:
   ```bash
   ./deploy.sh
   ```

4. **Access your application**:
   The deployment will output a CloudFront URL where your application is available.

## Manual Deployment Steps

If you prefer to deploy manually:

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Build Python layer**:
   ```bash
   ./build-layer.sh
   ```

3. **Bootstrap CDK** (first time only):
   ```bash
   npx cdk bootstrap --region us-east-2
   ```

4. **Deploy infrastructure**:
   ```bash
   npx cdk deploy
   ```

## Development

For local development of the frontend:

```bash
npm run dev
```

This starts a local HTTP server on port 8000. Note that API calls will fail in local development since they require the deployed Lambda functions.

## Usage

1. **Access the web interface** using the CloudFront URL from deployment
2. **Choose input method**:
   - Upload log files: Select one or more contest log files
   - Google Sheets: Provide a publicly accessible Google Sheets URL
3. **Enter contest information**:
   - Callsign (required)
   - Contest year
   - Operator and station categories
   - Grid square (required)
   - Power level (optional)
4. **Select desired outputs**:
   - Cabrillo log file (recommended)
   - Various analysis reports
   - Directional visualizations
   - Multi-log comparison (requires multiple files)
5. **Process and download** your results

## File Naming Convention

Generated files follow this pattern:
- `{CALLSIGN}_{TYPE}_{YYYY-MM-DD}.{ext}`
- Example: `K2UA_ARRL_10GHZ_2024-08-18.log`

## Supported Input Formats

- **Cabrillo files** (.log): Standard contest logging format
- **CSV files** (.csv): Comma-separated values with contest data
- **Text files** (.txt): Tab or space-delimited contest logs
- **Google Sheets**: Public or shared spreadsheets with contest data

## Required Google Sheets Format

For Google Sheets integration, ensure your sheet contains these columns:
- `date`: Contest date (YYYY-MM-DD format)
- `time`: UTC time (HHMM format)
- `call`: Station callsign
- `band`: Frequency band (e.g., "10GHz", "24GHz")
- `grid`: Station grid square
- `sourcegrid`: Your grid square (optional, can be filled from contest info)

## AWS Resources Created

The deployment creates these AWS resources:
- **S3 Buckets**: Website hosting and temporary file storage
- **Lambda Function**: Contest log processing (Python 3.11)
- **API Gateway**: REST API for processing requests
- **CloudFront Distribution**: Global content delivery
- **IAM Roles**: Least-privilege access for Lambda

## Cost Considerations

This serverless architecture is designed to be cost-effective:
- **S3**: Minimal storage costs, files auto-delete after 24 hours
- **Lambda**: Pay per processing request (typically <$0.01 per log)
- **API Gateway**: Pay per API call
- **CloudFront**: Free tier covers most amateur radio usage

## Security

- All file uploads are temporary (24-hour lifecycle)
- No persistent storage of contest data
- HTTPS-only access via CloudFront
- CORS properly configured for web access
- IAM roles follow least-privilege principles

## Troubleshooting

**Deployment Issues**:
- Ensure AWS CLI is configured: `aws sts get-caller-identity`
- Check region is set to us-east-2: `aws configure get region`
- Verify CDK is installed: `npx cdk --version`

**Processing Errors**:
- Verify Google Sheets URL is publicly accessible
- Check file formats match supported types
- Ensure required contest information is provided

**File Download Issues**:
- Download links expire after 1 hour
- Re-process if links have expired
- Check browser download settings

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review AWS CloudWatch logs for Lambda function errors
3. Verify input data format matches requirements

## License

This project is designed for amateur radio contest use and follows the same licensing as the original ARRL 10 GHz contest logging tools.