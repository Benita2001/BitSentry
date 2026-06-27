#!/usr/bin/env node

const { spawn } = require('child_process');
const path = require('path');
const os = require('os');

// Find python3 - check common locations
function findPython() {
  const candidates = [
    '/opt/miniconda3/bin/python3.13',
    '/opt/miniconda3/bin/python3',
    'python3.13',
    'python3',
    'python'
  ];
  return candidates[0]; // spawn will fail fast if not found
}

// Check if bitsentry is installed
function checkBitSentry(python) {
  return new Promise((resolve) => {
    const check = spawn(python, ['-c', 'import bitsentry; print("ok")'], {
      stdio: ['pipe', 'pipe', 'pipe']
    });
    check.stdout.on('data', (data) => {
      if (data.toString().trim() === 'ok') resolve(true);
    });
    check.on('error', () => resolve(false));
    check.on('close', (code) => {
      if (code !== 0) resolve(false);
    });
  });
}

async function main() {
  const python = findPython();

  // Check bitsentry is installed
  const installed = await checkBitSentry(python);
  if (!installed) {
    console.error('BitSentry is not installed. Run: pip install bitsentry');
    console.error('Then retry: npx @0xbeni/bitsentry-mcp');
    process.exit(1);
  }

  console.error('Starting BitSentry MCP server...');
  console.error('BitSentry v0.2.0 - Safety & Audit Layer for Bitget Agents');

  // Start the Python MCP server
  const server = spawn(python, ['-m', 'bitsentry.mcp.server'], {
    stdio: 'inherit',
    env: {
      ...process.env,
    }
  });

  server.on('error', (err) => {
    console.error('Failed to start BitSentry MCP server:', err.message);
    process.exit(1);
  });

  server.on('close', (code) => {
    process.exit(code || 0);
  });

  // Forward signals
  process.on('SIGINT', () => server.kill('SIGINT'));
  process.on('SIGTERM', () => server.kill('SIGTERM'));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
