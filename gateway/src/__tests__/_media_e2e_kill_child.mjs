// gateway/src/__tests__/_media_e2e_kill_child.mjs
// Child process for the P0.3-E3 kill-mid-upload test. Streams a real 50MB
// payload through the real downloadAndUploadMedia() to real MinIO, printing
// "PROGRESS" once real bytes have started flowing to MinIO (so the parent's
// SIGKILL lands mid-upload, not before it begins).
import { Redis } from 'ioredis';
import { S3Client } from '@aws-sdk/client-s3';
import { downloadAndUploadMedia } from '../media.js';

const [, , sessionId, providerMessageId] = process.argv;

const s3Client = new S3Client({
  endpoint: process.env.MINIO_TEST_ENDPOINT_URL || 'http://minio:9000',
  region: 'us-east-1',
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.MINIO_TEST_ACCESS_KEY,
    secretAccessKey: process.env.MINIO_TEST_SECRET_KEY,
  },
});
const redis = new Redis({
  host: process.env.REDIS_DURABLE_HOST || 'redis-durable',
  port: Number(process.env.REDIS_DURABLE_PORT || 6379),
  password: process.env.REDIS_DURABLE_PASSWORD,
});

async function* streamAndReportProgress(sizeBytes) {
  let remaining = sizeBytes;
  let sent = 0;
  const chunkSize = 64 * 1024;
  while (remaining > 0) {
    const n = Math.min(chunkSize, remaining);
    yield Buffer.alloc(n, 0x41);
    remaining -= n;
    sent += n;
    if (sent > 2 * 1024 * 1024) {
      process.stdout.write('PROGRESS\n');
    }
  }
}

const sizeBytes = 50 * 1024 * 1024;
downloadAndUploadMedia({
  content: { fileLength: sizeBytes },
  type: 'document',
  sessionId,
  providerMessageId,
  ext: 'bin',
  mimeType: 'application/octet-stream',
  redisClient: redis,
  s3Client,
  bucket: process.env.MINIO_TEST_BUCKET_NAME || 'sharwa-ai-test',
  downloadFn: async () => streamAndReportProgress(sizeBytes),
}).catch(() => {
  // The parent kills us before this can resolve either way in the normal
  // case; a real error here (rather than a kill) just exits non-zero.
  process.exit(1);
});
