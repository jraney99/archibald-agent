import assert from 'node:assert/strict';
import {cryptoDestinationArgv} from './crypto-sweep.mjs';

assert.equal(cryptoDestinationArgv({content:'hardware wallet'}), null);
assert.deepEqual(
  cryptoDestinationArgv({content:'hardware wallet', replyToId:'telegram:42'}),
  ['afrexai-personal-finance/scripts/finance_holdings.py', 'reply-destination',
   '--reply-id', '42', '--text', 'hardware wallet'],
);
