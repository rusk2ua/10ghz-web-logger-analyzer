import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export class Arrl10GhzWebStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // S3 bucket for website hosting
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      bucketName: `arrl-10ghz-web-${this.account}-${this.region}`,
      websiteIndexDocument: 'index.html',
      publicReadAccess: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ACLS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // S3 bucket for temporary file storage
    const filesBucket = new s3.Bucket(this, 'FilesBucket', {
      bucketName: `arrl-10ghz-files-${this.account}-${this.region}`,
      lifecycleRules: [{
        id: 'DeleteAfter1Day',
        expiration: cdk.Duration.days(1),
      }],
      cors: [{
        allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.POST, s3.HttpMethods.PUT],
        allowedOrigins: ['*'],
        allowedHeaders: ['*'],
      }],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Lambda layer for Python dependencies
    const pythonLayer = new lambda.LayerVersion(this, 'PythonDependencies', {
      code: lambda.Code.fromAsset('lambda/layers/python'),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_11],
      description: 'Python dependencies for ARRL contest processing',
    });

    // Lambda function for processing contest logs
    const processFunction = new lambda.Function(this, 'ProcessFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'process.handler',
      code: lambda.Code.fromAsset('lambda/functions/process'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      layers: [pythonLayer],
      environment: {
        FILES_BUCKET: filesBucket.bucketName,
      },
    });

    // Grant Lambda permissions to access S3
    filesBucket.grantReadWrite(processFunction);

    // API Gateway
    const api = new apigateway.RestApi(this, 'ProcessApi', {
      restApiName: 'ARRL 10 GHz Contest API',
      description: 'API for processing contest logs',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key'],
      },
    });

    const processIntegration = new apigateway.LambdaIntegration(processFunction);
    api.root.addResource('process').addMethod('POST', processIntegration);

    // Deploy frontend files to S3
    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [s3deploy.Source.asset('./frontend')],
      destinationBucket: websiteBucket,
    });

    // CloudFront distribution
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: new origins.S3Origin(websiteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(api),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
      },
    });

    // Outputs
    new cdk.CfnOutput(this, 'WebsiteURL', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'Website URL',
    });

    new cdk.CfnOutput(this, 'ApiURL', {
      value: api.url,
      description: 'API Gateway URL',
    });
  }
}