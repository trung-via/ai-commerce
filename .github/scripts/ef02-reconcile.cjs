'use strict';

// Read-only GitHub ref reconciliation for interrupted EF02 workflows.
// Input is the CURRENT Issue JSON, which may differ from the pinned run artifact.
// Never infer provider non-invocation, no-retry safety, verification or publication.
const {createHash} = require('node:crypto');
const MAX_ACTION_READS = 16;
const SHA = /^[a-f0-9]{40}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

function attemptKey(repoFullName, attemptId) {
  return createHash('sha256').update(repoFullName + ':' + attemptId).digest('hex').slice(0, 32);
}

async function readRef(github, owner, repo, ref) {
  try {
    const {data} = await github.rest.git.getRef({owner, repo, ref});
    const sha = data?.object?.sha;
    return SHA.test(sha || '') ? {state:'PRESENT', sha} : {state:'READ_UNKNOWN'};
  } catch (error) {
    return {state: error?.status === 404 ? 'ABSENT' : 'READ_UNKNOWN'};
  }
}

async function readCommit(github, owner, repo, sha) {
  try {
    const {data} = await github.rest.git.getCommit({owner, repo, commit_sha: sha});
    return data;
  } catch (_) {
    return null;
  }
}

async function inspectAction(github, repoFullName, sourceSha, action) {
  const [owner, repo] = repoFullName.split('/');
  const key = attemptKey(repoFullName, action.attempt_id);
  const entry = {action_id:action.action_id, attempt_id:action.attempt_id,
    claim:'READ_UNKNOWN', candidate:'READ_UNKNOWN'};
  if (action.reuse_candidate != null) {
    entry.claim = 'REUSE_NOT_A_NEW_CLAIM';
    entry.candidate = 'REUSE_REQUIRES_APPROVAL_REGISTRY_CHECK';
    return entry;
  }
  const claim = await readRef(github, owner, repo, 'heads/ef02/claims/' + key);
  if (claim.state === 'ABSENT') entry.claim = 'NO_CLAIM_REF_OBSERVED';
  else if (claim.state === 'READ_UNKNOWN') entry.claim = 'CLAIM_READ_UNKNOWN';
  else {
    const commit = await readCommit(github, owner, repo, claim.sha);
    const expectedSuffix = '\nattempt_id=' + action.attempt_id;
    const message = commit?.message || '';
    const parents = commit?.parents?.map(x => x.sha) || [];
    entry.claim = (parents.length === 1 && parents[0] === sourceSha &&
      /^EF02 CLAIM\n\nfingerprint=[a-f0-9]{64}\nattempt_id=/.test(message) &&
      message.endsWith(expectedSuffix))
      ? 'CLAIM_REF_VERIFIED' : 'CLAIM_REF_UNVERIFIED';
  }
  const candidate = await readRef(github, owner, repo, 'heads/ef02/candidate/' + key);
  if (candidate.state === 'ABSENT') entry.candidate = 'NO_CANDIDATE_REF_OBSERVED';
  else if (candidate.state === 'READ_UNKNOWN') entry.candidate = 'CANDIDATE_READ_UNKNOWN';
  else {
    const commit = await readCommit(github, owner, repo, candidate.sha);
    const parents = commit?.parents?.map(x => x.sha) || [];
    const parentValid = parents.length === 1 && SHA.test(parents[0]);
    const sourceValid = (action.depends_on || []).length > 0 ||
      (parentValid && parents[0] === sourceSha);
    entry.candidate = (commit?.message ===
      'EF02 candidate attempt ' + action.attempt_id &&
      parentValid && sourceValid && entry.claim === 'CLAIM_REF_VERIFIED')
      ? 'CANDIDATE_REF_VERIFIED_NOT_RUN_PASS' : 'CANDIDATE_REF_UNVERIFIED';
  }
  return entry;
}

async function reconcileIssue(github, {owner, repo, issue_number}) {
  let data;
  try {
    const result = await github.rest.issues.get({owner, repo, issue_number});
    data = result.data;
  } catch (_) {
    return {status:'READ_UNKNOWN', reason:'ISSUE_FETCH_FAILED', actions:[]};
  }
  if (data?.pull_request || !data?.title?.startsWith('[EF02]')) {
    return {status:'UNAVAILABLE', reason:'ISSUE_NOT_EF02', actions:[]};
  }
  let request;
  try { request = JSON.parse(data.body || ''); }
  catch (_) { return {status:'UNAVAILABLE', reason:'ISSUE_JSON_UNAVAILABLE', actions:[]}; }
  if (request?.kind !== 'AI_COMMERCE_REMOTE_ACTION_BATCH' ||
      !SHA.test(request.source_sha || '') || !Array.isArray(request.actions) ||
      request.actions.length === 0 ||
      !request.actions.every(a => a && ID.test(a.action_id || '') &&
        ID.test(a.attempt_id || '') && Array.isArray(a.depends_on))) {
    return {status:'UNAVAILABLE', reason:'ISSUE_BATCH_NOT_RECONCILABLE', actions:[]};
  }
  const repoFullName = owner + '/' + repo;
  const selected = request.actions.slice(0, MAX_ACTION_READS);
  const actions = [];
  for (const action of selected) {
    actions.push(await inspectAction(github, repoFullName, request.source_sha, action));
  }
  return {status: selected.length === request.actions.length ?
    'READ_COMPLETE' : 'READ_PARTIAL',
    reason:'CURRENT_ISSUE_BODY_NOT_PINNED_RUN_REQUEST',
    source_sha:request.source_sha,
    checked:actions.length, total:request.actions.length, actions};
}

function summary(report) {
  if (report.status === 'UNAVAILABLE' || report.status === 'READ_UNKNOWN') {
    return 'GitHub ref reconciliation: ' + report.status + ' (' + report.reason + ').';
  }
  const lines = ['GitHub ref reconciliation: ' + report.status +
    ' (' + report.checked + '/' + report.total + ' actions).',
    'Inputs: current Issue JSON, not the immutable run request artifact.'];
  for (const action of report.actions) {
    lines.push('- ' + action.action_id + ': claim=' + action.claim +
      ', candidate=' + action.candidate);
  }
  lines.push('A missing claim/candidate ref does NOT prove the provider was never invoked. ' +
    'A candidate ref does NOT prove tests passed. Inspect pinned artifacts and ' +
    'reconcile side effects before authorizing any new attempt; never blindly replay.');
  return lines.join('\n');
}

module.exports = {attemptKey, inspectAction, reconcileIssue, summary};
