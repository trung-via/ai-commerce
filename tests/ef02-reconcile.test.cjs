'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const reconcile = require('../.github/scripts/ef02-reconcile.cjs');

const repo = 'trung-via/ai-commerce', owner = 'trung-via', repoName = 'ai-commerce';
const source = 'a'.repeat(40);
const fingerprint = 'f'.repeat(64);

function action(id, extra = {}) {
  return {action_id:id, attempt_id:'attempt-' + id, depends_on:[], ...extra};
}
function packet(actions) {
  return {kind:'AI_COMMERCE_REMOTE_ACTION_BATCH', version:1,
    source_sha:source, actions};
}
function fake(actions, entries = {}, options = {}) {
  const accesses = [], posted = [];
  const request = packet(actions);
  const git = {
    async getRef({ref}) {
      accesses.push(['getRef', ref]);
      if (options.transportError) throw Object.assign(new Error('rate limited'),{status:403});
      const match = entries[ref];
      if (!match) throw Object.assign(new Error('missing'),{status:404});
      return {data:{object:{sha:match}}};
    },
    async getCommit({commit_sha}) {
      accesses.push(['getCommit', commit_sha]);
      const data = entries['commit:' + commit_sha];
      if (!data) throw Object.assign(new Error('missing'),{status:404});
      return {data};
    }
  };
  const api = {rest:{
    issues: {
      get: async () => ({data:{
        title: options.title || '[EF02] fixture', body: options.rawBody ??
          JSON.stringify(request), pull_request: options.isPR ? {} : undefined
      }}),
      listComments: async () => ({data:[]}),
      createComment: async arg => { posted.push(arg); return {data:{}}; }
    },
    git,
    actions: {listJobsForWorkflowRun: async () =>
      ({data:{jobs:[{name:'validate',conclusion:'success'},
        {name:'execute',status:'completed',conclusion:'cancelled'}]}})}
  }, paginate:async()=>[]};
  return {api, accesses, posted};
}
function bind(entries, a, {claim = true, candidate = true, claimParent = source,
  candidateParent = source, claimMessage, candidateMessage} = {}) {
  const key = reconcile.attemptKey(repo, a.attempt_id);
  if (claim) {
    entries['heads/ef02/claims/' + key] = 'b'.repeat(40);
    entries['commit:' + 'b'.repeat(40)] = {
      message: claimMessage ?? ('EF02 CLAIM\n\nfingerprint=' + fingerprint +
        '\nattempt_id=' + a.attempt_id), parents:[{sha:claimParent}]
    };
  }
  if (candidate) {
    entries['heads/ef02/candidate/' + key] = 'c'.repeat(40);
    entries['commit:' + 'c'.repeat(40)] = {
      message: candidateMessage ?? ('EF02 candidate attempt ' + a.attempt_id),
      parents:[{sha:candidateParent}]
    };
  }
}

test('claim/candidate exact refs and matching commit identities are observed, not labelled PASS', async () => {
  const a = action('success');
  const entries = {};
  bind(entries,a);
  const x = fake([a],entries);
  const r = await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:3});
  assert.equal(r.status,'READ_COMPLETE');
  assert.deepEqual(r.actions[0],{
    action_id:'success',attempt_id:'attempt-success',
    claim:'CLAIM_REF_VERIFIED',pre_spawn:'NO_PRESPAWN_REF_OBSERVED',
    spawned:'NO_SPAWNED_REF_OBSERVED',candidate:'CANDIDATE_REF_VERIFIED_NOT_RUN_PASS'
  });
  assert.match(reconcile.summary(r),/does NOT prove tests passed/);
  assert.equal(x.posted.length,0);
});

test('claim exists but no candidate is not an invitation to replay the provider', async () => {
  const a = action('claimed-no-output'), entries = {};
  bind(entries,a,{candidate:false});
  const r = await reconcile.reconcileIssue(fake([a],entries).api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(r.actions[0].claim,'CLAIM_REF_VERIFIED');
  assert.equal(r.actions[0].candidate,'NO_CANDIDATE_REF_OBSERVED');
  assert.match(reconcile.summary(r),/never blindly replay/);
});

test('blocked action has no observed ref but its provider outcome stays unproven', async () => {
  const r = await reconcile.reconcileIssue(fake([action('blocked')]).api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(r.actions[0].claim,'NO_CLAIM_REF_OBSERVED');
  assert.equal(r.actions[0].candidate,'NO_CANDIDATE_REF_OBSERVED');
  assert.match(reconcile.summary(r),/does NOT prove the provider was never invoked/);
});

test('mismatched claim source and candidate provenance never appear verified', async () => {
  const a = action('tampered'), entries = {};
  bind(entries,a,{claimParent:'d'.repeat(40)});
  const r = await reconcile.reconcileIssue(fake([a],entries).api,
    {owner,repo:repoName,issue_number:3});
  assert.equal(r.actions[0].claim,'CLAIM_REF_UNVERIFIED');
  assert.equal(r.actions[0].candidate,'CANDIDATE_REF_UNVERIFIED');
});

test('candidate with parent mismatch is not accepted for independent action', async () => {
  const a = action('wrong-parent'), entries={};
  bind(entries,a,{candidateParent:'e'.repeat(40)});
  const r = await reconcile.reconcileIssue(fake([a],entries).api,
    {owner,repo:repoName,issue_number:3});
  assert.equal(r.actions[0].claim,'CLAIM_REF_VERIFIED');
  assert.equal(r.actions[0].candidate,'CANDIDATE_REF_UNVERIFIED');
});

test('dependent candidate may have an integration parent distinct from request source', async () => {
  const a = action('dependent',{depends_on:['parent']}), entries={};
  bind(entries,a,{candidateParent:'d'.repeat(40)});
  const r = await reconcile.reconcileIssue(fake([a],entries).api,
    {owner,repo:repoName,issue_number:3});
  assert.equal(r.actions[0].candidate,'CANDIDATE_REF_VERIFIED_NOT_RUN_PASS');
});

test('GitHub API transport faults report READ_UNKNOWN, not missing refs or safe retry', async () => {
  const x = fake([action('transport')],{},{transportError:true});
  const r = await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(r.actions[0].claim,'CLAIM_READ_UNKNOWN');
  assert.equal(r.actions[0].candidate,'CANDIDATE_READ_UNKNOWN');
  assert.match(reconcile.summary(r),/never blindly replay/);
});

test('invalid and empty Issue JSON cannot claim authoritative reconciliation', async () => {
  const a = action('empty');
  for (const rawBody of ['not JSON', '{}', 'null']) {
    const x = fake([a],{},{rawBody});
    const r = await reconcile.reconcileIssue(x.api,
      {owner,repo:repoName,issue_number:8});
    assert.equal(r.status,'UNAVAILABLE');
    assert.equal(x.accesses.length,0);
  }
});

test('untrusted issue edited to another type causes no ref lookups', async () => {
  const x = fake([action('fake')],{},{title:'not an EF02 issue'});
  const r = await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(r.status,'UNAVAILABLE');
  assert.equal(x.accesses.length,0);
});

test('approved reuse is explicitly not treated as a fresh claim or verified candidate', async () => {
  const x = fake([action('reuse',{reuse_candidate:{candidate_sha:'c'.repeat(40)}})]);
  const r = await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:10});
  assert.equal(r.actions[0].claim,'REUSE_NOT_A_NEW_CLAIM');
  assert.equal(r.actions[0].candidate,'REUSE_REQUIRES_APPROVAL_REGISTRY_CHECK');
  assert.equal(x.accesses.length,0);
});

test('large request produces a partial bounded read and labels all uninspected actions', async () => {
  const actions = Array.from({length:19},(_,i)=>action('a'+i));
  const x = fake(actions);
  const r = await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(r.status,'READ_PARTIAL');
  assert.equal(r.checked,16);
  assert.equal(r.total,19);
  assert.match(reconcile.summary(r),/16\/19 actions/);
});

test('terminal cancellation observer includes read-only reconciliation and never writes ref', async () => {
  const observe = require('../.github/scripts/ef02-watch.cjs');
  const a = action('interrupted'), entries={};
  bind(entries,a,{candidate:false});
  const x = fake([a],entries);
  const context = {
    repo:{owner,repo:repoName}, eventName:'workflow_run',
    payload:{workflow_run:{
      id:9999, name:'EF02 Issue #8', display_title:'EF02 Issue #8',
      head_repository:{full_name:repo}, status:'completed', conclusion:'cancelled',
      html_url:'https://github.com/trung-via/ai-commerce/actions/runs/9999'
    }}
  };
  const count = await observe({github:x.api,context,core:{info:()=>{}}});
  assert.equal(count,1);
  assert.equal(x.posted.length,1);
  assert.match(x.posted[0].body,/WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
  assert.match(x.posted[0].body,/CLAIM_REF_VERIFIED/);
  assert.match(x.posted[0].body,/NO_CANDIDATE_REF_OBSERVED/);
  assert.equal(x.accesses.every(([op])=>op==='getRef'||op==='getCommit'),true);
});


test('remote pre-spawn GitHub ref proves only boundary, never provider invocation', async () => {
  const a=action('remote-boundary'), entries={};
  bind(entries,a,{candidate:false});
  const key=reconcile.attemptKey(repo,a.attempt_id);
  const claimSha='b'.repeat(40), markSha='d'.repeat(40), treeSha='a'.repeat(40);
  entries['commit:'+claimSha].tree={sha:treeSha};
  entries['heads/ef02/pre-spawn/'+key]=markSha;
  entries['commit:'+markSha]={
    message:'EF02 PRESPAWN\n\nfingerprint='+fingerprint+'\nattempt_id='+a.attempt_id,
    tree:{sha:treeSha},parents:[{sha:claimSha}]
  };
  const x=fake([a],entries);
  const report=await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(report.actions[0].claim,'CLAIM_REF_VERIFIED');
  assert.equal(report.actions[0].pre_spawn,'PRESPAWN_REF_VERIFIED_NOT_PROVIDER_PROOF');
  assert.equal(report.actions[0].candidate,'NO_CANDIDATE_REF_OBSERVED');
  assert.match(reconcile.summary(report),/PRESPAWN_REF_VERIFIED_NOT_PROVIDER_PROOF/);
  assert.equal(x.accesses.every(([kind])=>kind==='getRef'||kind==='getCommit'),true);
});

test('forged pre-spawn ref parent or fingerprint is never verified', async () => {
  for (const invalid of ['parent','fingerprint','tree']) {
    const a=action('tamper-'+invalid), entries={};
    bind(entries,a,{candidate:false});
    const key=reconcile.attemptKey(repo,a.attempt_id);
    const claimSha='b'.repeat(40), markSha='d'.repeat(40), treeSha='a'.repeat(40);
    entries['commit:'+claimSha].tree={sha:treeSha};
    entries['heads/ef02/pre-spawn/'+key]=markSha;
    entries['commit:'+markSha]={
      message:'EF02 PRESPAWN\n\nfingerprint='+
        (invalid==='fingerprint'?'e'.repeat(64):fingerprint)+'\nattempt_id='+a.attempt_id,
      parents:[{sha:invalid==='parent'?'c'.repeat(40):claimSha}],
      tree:{sha:invalid==='tree'?'d'.repeat(40):treeSha}
    };
    const report=await reconcile.reconcileIssue(fake([a],entries).api,
      {owner,repo:repoName,issue_number:8});
    assert.equal(report.actions[0].pre_spawn,'PRESPAWN_REF_UNVERIFIED', invalid);
  }
});

test('unreadable remote checkpoint does not become ABSENT or safe replay', async () => {
  const a=action('unknown-stage'), entries={};
  bind(entries,a,{candidate:false});
  const x=fake([a],entries,{transportError:true});
  const report=await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(report.actions[0].pre_spawn,'PRESPAWN_READ_UNKNOWN');
  assert.match(reconcile.summary(report),/never blindly replay/);
});

test('reuse never reads or manufactures a new remote pre-spawn marker', async () => {
  const a=action('reuse-stage',{reuse_candidate:{candidate_sha:'c'.repeat(40)}});
  const x=fake([a]);
  const report=await reconcile.reconcileIssue(x.api,
    {owner,repo:repoName,issue_number:8});
  assert.equal(report.actions[0].pre_spawn,'REUSE_NO_NEW_PRESPAWN');
  assert.equal(x.accesses.length,0);
});


test('post-spawn remote PID checkpoint is chained to verified pre-spawn, never provider proof', async () => {
  const a=action('pid-boundary'), entries={};
  bind(entries,a,{candidate:false});
  const key=reconcile.attemptKey(repo,a.attempt_id);
  const claimSha='b'.repeat(40), preSha='d'.repeat(40), spawnedSha='e'.repeat(40);
  const treeSha='a'.repeat(40);
  entries['commit:'+claimSha].tree={sha:treeSha};
  entries['heads/ef02/pre-spawn/'+key]=preSha;
  entries['commit:'+preSha]={
    message:'EF02 PRESPAWN\n\nfingerprint='+fingerprint+'\nattempt_id='+a.attempt_id,
    tree:{sha:treeSha},parents:[{sha:claimSha}]};
  entries['heads/ef02/spawned/'+key]=spawnedSha;
  entries['commit:'+spawnedSha]={
    message:'EF02 SPAWNED\n\nfingerprint='+fingerprint+'\nattempt_id='+a.attempt_id+
      '\nchild_pid=4312',
    tree:{sha:treeSha},parents:[{sha:preSha}]};
  const x=fake([a],entries);
  const report=await reconcile.reconcileIssue(x.api,{owner,repo:repoName,issue_number:8});
  assert.equal(report.actions[0].spawned,'SPAWNED_PID_REF_VERIFIED_NOT_PROVIDER_PROOF');
  assert.equal(report.actions[0].candidate,'NO_CANDIDATE_REF_OBSERVED');
  assert.match(reconcile.summary(report),/SPAWNED_PID_REF_VERIFIED_NOT_PROVIDER_PROOF/);
  assert.equal(x.accesses.every(([op])=>op==='getRef'||op==='getCommit'),true);
});

test('forged spawned checkpoints cannot prove even a child PID', async () => {
  for (const invalid of ['parent','fingerprint','tree','pid']) {
    const a=action('bad-pid-'+invalid), entries={};
    bind(entries,a,{candidate:false});
    const key=reconcile.attemptKey(repo,a.attempt_id);
    const claim='b'.repeat(40), pre='d'.repeat(40), spawned='e'.repeat(40);
    const tree='a'.repeat(40);
    entries['commit:'+claim].tree={sha:tree};
    entries['heads/ef02/pre-spawn/'+key]=pre;
    entries['commit:'+pre]={
      message:'EF02 PRESPAWN\n\nfingerprint='+fingerprint+'\nattempt_id='+a.attempt_id,
      tree:{sha:tree},parents:[{sha:claim}]};
    entries['heads/ef02/spawned/'+key]=spawned;
    entries['commit:'+spawned]={
      message:'EF02 SPAWNED\n\nfingerprint='+
        (invalid==='fingerprint'?'0'.repeat(64):fingerprint)+
        '\nattempt_id='+a.attempt_id+'\nchild_pid='+(invalid==='pid'?'0':'4312'),
      tree:{sha:invalid==='tree'?'f'.repeat(40):tree},
      parents:[{sha:invalid==='parent'?'f'.repeat(40):pre}]};
    const report=await reconcile.reconcileIssue(fake([a],entries).api,
      {owner,repo:repoName,issue_number:8});
    assert.equal(report.actions[0].spawned,'SPAWNED_REF_UNVERIFIED',invalid);
  }
});

test('unreadable spawned checkpoint is UNKNOWN, not permission to retry', async () => {
  const a=action('unreadable-spawned'), entries={};
  bind(entries,a,{candidate:false});
  const x=fake([a],entries,{transportError:true});
  const report=await reconcile.reconcileIssue(x.api,{owner,repo:repoName,issue_number:8});
  assert.equal(report.actions[0].spawned,'SPAWNED_READ_UNKNOWN');
  assert.match(reconcile.summary(report),/never blindly replay/);
});
