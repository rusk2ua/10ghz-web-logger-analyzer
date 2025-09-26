#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Arrl10GhzWebStack } from './arrl-10ghz-web-stack';

const app = new cdk.App();
new Arrl10GhzWebStack(app, 'Arrl10GhzWebStack', {
  env: {
    region: 'us-east-2',
  },
});