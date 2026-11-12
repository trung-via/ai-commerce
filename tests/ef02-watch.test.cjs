'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const observe = require('../.github/scripts/ef02-watch.cjs');

const owner = 'trung-via', repo = 'ai-commerce';
function run(overrides = {}) {
  return {id: 555, name: 'EF02 Issue #19',
    head_repository: {full_name: 'trung-via/ai-commerce'},
    display_title: 'EF02 Issue #19', status: 'completed',
    conclusion: 'cancelled', created_at: '2026-10-28T08:00:00Z',
    html_url: 'https://github.com/trung-via/ai-commerce/actions/runs/555',
    ...overrides};
}
function fake({event = 'workflow_run', result = run(), jobs = [],
  comments = [], workflowRuns = [], title = '[EF02] M0-R check'} = {}) {
  const posted = [];
  const api = {
    rest: {
      issues: {
        get: async () => ({data: {title, pull_request: undefined}}),
        listComments: async () => ({data: comments}),
        createComment: async x => { posted.push(x); return {data: {}}; }
      },
      actions: {
        listJobsForWorkflowRun: async () => ({data: {jobs}}),
        listWorkflowRuns: async () => ({data: {workflow_runs: workflowRuns}})
      }
    },
    paginate: async (_method, _args) => comments
  };
  const context = {repo: {owner, repo}, eventName: event,
    payload: {workflow_run: result}};
  return {api, context, posted, core: {info: () => {}}};
}

test('bind only a numbered EF02 run in the expected repository', () => {
  assert.equal(observe.issueNumber(run(), 'trung-via/ai-commerce'), 19);
  // Actual GitHub workflow_run uses custom run-name as run.name.
  assert.equal(observe.issueNumber(run({name: 'EF02 Issue #19'}), 'trung-via/ai-commerce'), 19);
  assert.equal(observe.issueNumber(run({display_title: 'Unrelated #19'}), 'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({head_repository: {full_name: 'other/fork'}}), 'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({display_title: 'EF02 Issue #0'}), 'trung-via/ai-commerce'), null);
});

test('issue-ingress titles bind the exact EF02 issue, not neighboring workflows', () => {
  const wrapped = '[EF02][Production Q1-Q3][AGY fallback] after #814 quota (#815, opened)';
  assert.equal(observe.issueNumber(run({display_title:wrapped}), 'trung-via/ai-commerce'), 815);
  assert.equal(observe.issueNumber(run({display_title:wrapped.replace('opened', 'labeled')}),
    'trung-via/ai-commerce'), 815);
  assert.equal(observe.issueNumber(run({display_title:wrapped.replace('opened', 'created')}),
    'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({display_title:'[HOSTOPS READ] Probe (#815, opened)'}),
    'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({display_title:wrapped,
    head_repository:{full_name:'other/repo'}}), 'trung-via/ai-commerce'), null);
});

test('issue-ingress reusable EF02 failed jobs are reported once by scheduled backfill', async () => {
  const failed = run({id:815,
    display_title:'[EF02][Production Q1-Q3] shot trace (#815, opened)',
    status:'completed', conclusion:'failure', updated_at:'2026-10-28T08:55:00Z'});
  const x = fake({event:'schedule', workflowRuns:[failed], jobs:[
    {name:'ef02 / validate', conclusion:'success'},
    {name:'ef02 / execute', status:'completed', conclusion:'failure'}
  ]});
  assert.equal(await observe({github:x.api, context:x.context, core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}), 1);
  assert.equal(x.posted.length, 1, 'both workflow listings must not duplicate a run');
  assert.match(x.posted[0].body, /WORKFLOW_NONPASS_UNRECONCILED/);
  assert.match(x.posted[0].body, /side effects/);
});

test('wrapped EF02 queued execution stays UNKNOWN rather than falsely OFFLINE', () => {
  const wrapped = run({display_title:'[EF02] shot trace (#815, labeled)',
    status:'in_progress', conclusion:null});
  const status = observe.classifyQueued(wrapped, [
    {name:'ef02 / validate', conclusion:'success'},
    {name:'ef02 / execute', status:'queued'}
  ], Date.parse('2026-10-28T09:10:00Z'));
  assert.equal(status.code, 'WINDOWS_JOB_WAITING_UNCONFIRMED');
  assert.match(status.detail, /Runner offline is NOT established/);
});

test('observer watches both direct EF02 and issue-ingress parent workflows', () => {
  const fs = require('node:fs'), path = require('node:path');
  const yml = fs.readFileSync(path.join(__dirname, '../.github/workflows/ef02-watch.yml'), 'utf8');
  assert.match(yml, /EF02 Multi-Action remote engineering/);
  assert.match(yml, /AI Commerce Issue ingress \(EF02 \/ HostOps Personal\)/);
  const script = fs.readFileSync(path.join(__dirname, '../.github/scripts/ef02-watch.cjs'), 'utf8');
  assert.match(script, /'issue-ingress.yml'/);
});

test('cancelled after possible execution is UNCERTAIN, never PASS', async () => {
  const x = fake({jobs: [{name: 'validate', conclusion: 'success'},
                         {name: 'execute', status: 'completed', conclusion: 'cancelled'}]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 1);
  assert.equal(x.posted.length, 1);
  assert.match(x.posted[0].body, /WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
  assert.match(x.posted[0].body, /side effects are UNKNOWN/);
  assert.match(x.posted[0].body, /does not run executors/);
  assert.doesNotMatch(x.posted[0].body, /CANDIDATE_READY/);
});

test('validation failed and Windows skipped means hosted ingress rejected', async () => {
  const x = fake({result: run({conclusion: 'failure'}), jobs: [
    {name: 'validate', conclusion: 'failure'},
    {name: 'execute', conclusion: 'skipped'}
  ]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 1);
  assert.match(x.posted[0].body, /HOSTED_INGRESS_REJECTED/);
  assert.match(x.posted[0].body, /Windows execution was skipped/);
});

test('existing GitHub validation feedback prevents duplicate comments', async () => {
  const x = fake({result: run({conclusion: 'failure'}), jobs: [
    {name: 'validate', conclusion: 'failure'}, {name: 'execute', conclusion: 'skipped'}
  ], comments: [{
    user: {login: 'github-actions[bot]'},
    body: 'EF02 request REJECTED before Windows execution.\nReason: `TEST`\nWorkflow: https://github.com/trung-via/ai-commerce/actions/runs/555'
  }]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 0);
  assert.equal(x.posted.length, 0);
});

test('successful workflow and unrelated workflows do not produce comments', async () => {
  const x = fake({result: run({conclusion: 'success'})});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 0);
  const y = fake({result: run({display_title: 'unrelated run', conclusion: 'cancelled'})});
  assert.equal(await observe({github: y.api, context: y.context, core: y.core}), 0);
});

test('queued Windows job older than threshold is WAITING, not claimed OFFLINE', async () => {
  const base = run({status: 'in_progress', conclusion: null});
  const jobs = [{name: 'validate', conclusion: 'success'}, {name: 'execute', status: 'queued'}];
  const x = fake({event: 'schedule', jobs, workflowRuns: [base]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core},
    {nowMs: Date.parse('2026-10-28T09:00:00Z')}), 1);
  assert.match(x.posted[0].body, /WINDOWS_JOB_WAITING_UNCONFIRMED/);
  assert.match(x.posted[0].body, /Runner offline is NOT established/);
  assert.doesNotMatch(x.posted[0].body, /runner is offline/i);
});

test('no false queued report before threshold or without queued execute', async () => {
  const base = run({status: 'in_progress', conclusion: null});
  const recent = fake({event: 'schedule', jobs: [
    {name: 'validate', conclusion: 'success'}, {name: 'execute', status: 'queued'}],
    workflowRuns: [base]});
  assert.equal(await observe({github: recent.api, context: recent.context,
    core: recent.core}, {nowMs: Date.parse('2026-10-28T08:40:00Z')}), 0);
  const busy = fake({event: 'schedule', jobs: [
    {name: 'validate', conclusion: 'success'}, {name: 'execute', status: 'in_progress'}],
    workflowRuns: [base]});
  assert.equal(await observe({github: busy.api, context: busy.context,
    core: busy.core}, {nowMs: Date.parse('2026-10-28T09:00:00Z')}), 0);
  assert.equal(recent.posted.length + busy.posted.length, 0);
});

test('observer marker makes repeated scheduled check idempotent', async () => {
  const waiting = run({status: 'queued', conclusion: null});
  const jobs = [{name: 'validate', conclusion: 'success'}, {name: 'execute', status: 'queued'}];
  const x = fake({event: 'schedule', jobs, workflowRuns: [waiting],
    comments: [{user: {login: 'github-actions[bot]'},
      body: 'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED'}]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core},
    {nowMs: Date.parse('2026-10-28T09:00:00Z')}), 0);
  assert.equal(x.posted.length, 0);
});

test('non-EF02 source issue gets no comment', async () => {
  const x = fake({title: 'unrelated issue'});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 0);
  assert.equal(x.posted.length, 0);
});

test('EF02 workflow run title quotes the Issue number to avoid YAML comments', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const workflow = fs.readFileSync(path.join(__dirname, '../.github/workflows/ef02-batch.yml'), 'utf8');
  const line = workflow.split(/\r?\n/).find(x => x.startsWith('run-name: '));
  assert.ok(line, 'workflow should bind Issue number to run title');
  assert.ok(line.startsWith('run-name: "EF02 Issue #'), 'YAML # must be quoted');
  assert.ok(line.endsWith('"'), 'run-name must close its quote');
  assert.ok(line.includes('github.event.issue.number || inputs.issue_number'));
});

test('owner-hosted cancellation with skipped validate and Windows is confirmed pre-executor', async () => {
  const x = fake({jobs:[
    {name:'m0r_pre_executor_cancel', status:'completed', conclusion:'cancelled'},
    {name:'validate', status:'completed', conclusion:'skipped'},
    {name:'execute', status:'completed', conclusion:'skipped'}
  ]});
  const status = observe.classifyTerminal(x.context.payload.workflow_run, [
    {name:'m0r_pre_executor_cancel', conclusion:'cancelled'},
    {name:'validate', conclusion:'skipped'},
    {name:'execute', conclusion:'skipped'}
  ]);
  assert.equal(status.code,'WORKFLOW_CANCELLED_PRE_EXECUTOR_NO_WINDOWS');
  assert.match(status.detail,/never invoked/);
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),1);
  assert.match(x.posted[0].body,/WORKFLOW_CANCELLED_PRE_EXECUTOR_NO_WINDOWS/);
  assert.doesNotMatch(x.posted[0].body,/candidate=CLAIM_REF_VERIFIED/);
});

test('cancellation after Windows was started remains uncertain, not safe', () => {
  const status = observe.classifyTerminal(run(),[
    {name:'m0r_pre_executor_cancel', conclusion:'skipped'},
    {name:'validate', conclusion:'success'},
    {name:'execute', conclusion:'cancelled'}
  ]);
  assert.equal(status.code,'WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN');
  assert.match(status.detail,/side effects are UNKNOWN/);
});

test('retired M0-R cancellation probe cannot bypass standard EF02 hosted authorization', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const text = fs.readFileSync(path.join(__dirname, '../.github/workflows/ef02-batch.yml'), 'utf8');
  const validate = text.slice(text.indexOf('  validate:'), text.indexOf('  execute:'));
  const execute = text.slice(text.indexOf('  execute:'));
  assert.ok(!text.includes('  m0r_pre_executor_cancel:'), 'retired probe must not be runnable');
  assert.ok(!text.includes('cancelWorkflowRun('), 'no one-shot workflow cancellation');
  assert.ok(validate.includes("startsWith(github.event.issue.title, '[EF02]')"));
  assert.ok(validate.includes("data.pull_request || data.state !== 'open' || !data.title.startsWith('[EF02]')"));
  assert.ok(validate.includes("core.setFailed('RETIRED_WP_NOT_DISPATCHABLE')"));
  assert.ok(validate.includes("core.setOutput('issuer', data.user.login)"));
  assert.ok(validate.includes('python -m ai_commerce.remote validate'));
  assert.ok(validate.includes('--issuer-login "$env:EF02_ISSUER" --owner-login "$env:EF02_OWNER"'));
  assert.ok(validate.includes("shell: pwsh"));
  assert.ok(validate.includes("$validationExit = $LASTEXITCODE"));
  assert.ok(execute.includes("needs.validate.result == 'success'"));
  assert.ok(execute.includes('runs-on: [self-hosted, Windows, ai-commerce-ef02]'));
  // Two EF02 runner instances share one Windows account, not site-packages.
  // The execute job must use the per-job isolated Python interpreter.
  assert.ok(execute.includes('EF02_ISOLATED_PYTHON_NOT_SET'));
  assert.ok(execute.includes('& $venvPython -m ai_commerce.remote run'));
  assert.ok(execute.includes('--issuer-login "$env:EF02_ISSUER" --owner-login "$env:EF02_OWNER"'));
});

test('queued warning with run URL must NOT silence subsequent cancelled terminal outcome', async () => {
  const oldWarning = [
    'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED',
    'Status: \`WINDOWS_JOB_WAITING_UNCONFIRMED\`',
    'Workflow: https://github.com/trung-via/ai-commerce/actions/runs/555'
  ].join('\n\n');
  const x = fake({jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',status:'completed',conclusion:'cancelled'}
  ],comments:[{user:{login:'github-actions[bot]'},body:oldWarning}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),1);
  assert.equal(x.posted.length,1);
  assert.match(x.posted[0].body,/WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
  assert.match(x.posted[0].body,/side effects are UNKNOWN/);
  assert.match(x.posted[0].body,/before any new attempt/);
  assert.match(x.posted[0].body,/GitHub ref reconciliation: UNAVAILABLE/);
});

test('queued warning must NOT silence terminal failed after executed job', async () => {
  const x = fake({result:run({conclusion:'failure'}),jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',status:'completed',conclusion:'failure'}
  ],comments:[{user:{login:'github-actions[bot]'},
    body:'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED\n\nWorkflow: https://github.com/trung-via/ai-commerce/actions/runs/555'}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),1);
  assert.match(x.posted[0].body,/WORKFLOW_NONPASS_UNRECONCILED/);
});

test('terminal outcome observer marker is idempotent after queue to terminal transition', async () => {
  const x = fake({comments:[
    {user:{login:'github-actions[bot]'},
      body:'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED'},
    {user:{login:'github-actions[bot]'},
      body:'EF02_OBSERVER run=555 code=WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN'}
  ]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),0);
  assert.equal(x.posted.length,0);
});

test('unrelated bot comment linking the workflow is not a terminal receipt', async () => {
  const x = fake({comments:[{user:{login:'github-actions[bot]'},
    body:'Administrative note: https://github.com/trung-via/ai-commerce/actions/runs/555'}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),1);
  assert.match(x.posted[0].body,/WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
});

test('exact worker result for same run prevents redundant terminal observer report', async () => {
  const x = fake({result:run({conclusion:'failure'}),comments:[
    {user:{login:'github-actions[bot]'},
      body:'EF02 batch batch-123: PARTIAL_OR_FAILED\nGitHub workflow run: 555\nResults: 0/1'}
  ]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core}),0);
  assert.equal(x.posted.length,0);
});


test('scheduled observer recovers missed terminal callback without a model retry', async () => {
  const cancelled = run({status:'completed',conclusion:'cancelled',
    updated_at:'2026-10-28T08:55:00Z'});
  const x = fake({event:'schedule', workflowRuns:[cancelled], jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',conclusion:'cancelled',status:'completed'}
  ]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),1);
  assert.equal(x.posted.length,1);
  assert.match(x.posted[0].body,/WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
  assert.match(x.posted[0].body,/side effects are UNKNOWN/);
  assert.match(x.posted[0].body,/does not run executors, cancel jobs, mutate claims or retry models/);
});

test('scheduled backfill upgrades queued warning into a terminal failure', async () => {
  const failed = run({status:'completed',conclusion:'failure',
    updated_at:'2026-10-28T08:55:00Z'});
  const x = fake({event:'schedule',workflowRuns:[failed],jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',conclusion:'failure',status:'completed'}
  ],comments:[{user:{login:'github-actions[bot]'},
    body:'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED\n\nWorkflow: https://github.com/trung-via/ai-commerce/actions/runs/555'}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),1);
  assert.match(x.posted[0].body,/WORKFLOW_NONPASS_UNRECONCILED/);
});

test('scheduled terminal backfill is idempotent when workflow_run observer already posted', async () => {
  const failed = run({status:'completed',conclusion:'failure',
    updated_at:'2026-10-28T08:55:00Z'});
  const x = fake({event:'schedule',workflowRuns:[failed],jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',conclusion:'failure'}
  ],comments:[{user:{login:'github-actions[bot]'},
    body:'EF02_OBSERVER run=555 code=WORKFLOW_NONPASS_UNRECONCILED'}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
  assert.equal(x.posted.length,0);
});

test('scheduled terminal backfill honors post-completion grace and bounded lookback', async () => {
  const recent = run({status:'completed',conclusion:'cancelled',
    updated_at:'2026-10-28T09:08:00Z'});
  const old = run({id:556,display_title:'EF02 Issue #20',
    status:'completed',conclusion:'failure',updated_at:'2026-10-26T08:00:00Z'});
  const missingTimestamp = run({id:557,display_title:'EF02 Issue #21',
    status:'completed',conclusion:'failure'});
  const future = run({id:558,display_title:'EF02 Issue #22',
    status:'completed',conclusion:'cancelled',updated_at:'2026-10-28T09:20:00Z'});
  const x = fake({event:'schedule',workflowRuns:[recent,old,missingTimestamp,future]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
  assert.equal(x.posted.length,0);
});

test('scheduled recovery ignores success, unrelated issue and skipped primary workflow', async () => {
  const t = '2026-10-28T08:55:00Z';
  const x = fake({event:'schedule',workflowRuns:[
    run({id:10,status:'completed',conclusion:'success',updated_at:t}),
    run({id:11,status:'completed',conclusion:'skipped',updated_at:t}),
    run({id:12,status:'completed',conclusion:'cancelled',updated_at:t,
      display_title:'Non-EF02 probe'}),
    run({id:13,status:'completed',conclusion:'failure',updated_at:t,
      head_repository:{full_name:'other/repo'}})
  ]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
  assert.equal(x.posted.length,0);
});

test('scheduled terminal recovery detects timeout and leaves provider side effects UNKNOWN', async () => {
  const timedOut = run({status:'completed',conclusion:'timed_out',
    updated_at:'2026-10-28T08:55:00Z'});
  const x = fake({event:'schedule',workflowRuns:[timedOut],jobs:[
    {name:'validate',conclusion:'success'},
    {name:'execute',conclusion:'timed_out'}
  ]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),1);
  assert.match(x.posted[0].body,/WORKFLOW_TIMED_OUT_EXECUTION_UNCERTAIN/);
  assert.match(x.posted[0].body,/side effects are UNKNOWN/);
});


test('running EF02 execute older than two hours reports uncertainty, never OFFLINE', async () => {
  const active = run({status:'in_progress', conclusion:null});
  const jobs = [{name:'validate', conclusion:'success'},
    {name:'execute',status:'in_progress',started_at:'2026-10-28T07:00:00Z'}];
  const x = fake({event:'schedule', workflowRuns:[active], jobs});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),1);
  assert.equal(x.posted.length,1);
  assert.match(x.posted[0].body,/WINDOWS_EXECUTION_LONG_RUNNING_UNCONFIRMED/);
  assert.match(x.posted[0].body,/runner offline is NOT established/);
  assert.match(x.posted[0].body,/provider side effects are UNKNOWN/);
  assert.match(x.posted[0].body,/GitHub ref reconciliation: UNAVAILABLE/);
  assert.match(x.posted[0].body,/does not run executors, cancel jobs, mutate claims or retry models/);
});

test('execution age uses real Windows job started_at; not workflow creation time', async () => {
  const active = run({status:'in_progress', conclusion:null,
    created_at:'2026-10-28T02:00:00Z'});
  for (const started_at of [undefined,'2026-10-28T08:00:00Z','nonsense',
                              '2026-10-28T09:20:00Z']) {
    const jobs = [{name:'validate',conclusion:'success'},
      {name:'execute',status:'in_progress',started_at}];
    const x = fake({event:'schedule',workflowRuns:[active],jobs});
    assert.equal(await observe({github:x.api,context:x.context,core:x.core},
      {nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
    assert.equal(x.posted.length,0);
  }
});

test('running Windows advisory needs hosted validation and active execute job', () => {
  const active = run({status:'in_progress',conclusion:null});
  const started_at = '2026-10-28T05:00:00Z';
  assert.equal(observe.classifyQueued(active,[
    {name:'validate',conclusion:'failure'},
    {name:'execute',status:'in_progress',started_at}
  ],Date.parse('2026-10-28T09:00:00Z')),null);
  assert.equal(observe.classifyQueued(active,[
    {name:'validate',conclusion:'success'},
    {name:'execute',status:'completed',started_at}
  ],Date.parse('2026-10-28T09:00:00Z')),null);
});

test('queue warning can upgrade to running warning, then terminal, and remains idempotent', async () => {
  const queueMarker = 'EF02_OBSERVER run=555 code=WINDOWS_JOB_WAITING_UNCONFIRMED';
  const activeMarker = 'EF02_OBSERVER run=555 code=WINDOWS_EXECUTION_LONG_RUNNING_UNCONFIRMED';
  const active = run({status:'in_progress',conclusion:null});
  const jobs = [{name:'validate',conclusion:'success'},
    {name:'execute',status:'in_progress',started_at:'2026-10-28T06:00:00Z'}];
  const comments = [{user:{login:'github-actions[bot]'},body:queueMarker}];
  const x = fake({event:'schedule',workflowRuns:[active],jobs,comments});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),1);
  assert.match(x.posted[0].body,/WINDOWS_EXECUTION_LONG_RUNNING_UNCONFIRMED/);
  const duplicate = fake({event:'schedule',workflowRuns:[active],jobs,
    comments:comments.concat([{user:{login:'github-actions[bot]'},body:activeMarker}])});
  assert.equal(await observe({github:duplicate.api,context:duplicate.context,
    core:duplicate.core},{nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
  const terminal = fake({event:'workflow_run',result:run({status:'completed',
    conclusion:'cancelled'}),jobs:[
      {name:'validate',conclusion:'success'},
      {name:'execute',conclusion:'cancelled',status:'completed'}],
    comments:comments.concat([{user:{login:'github-actions[bot]'},body:activeMarker}])});
  assert.equal(await observe({github:terminal.api,context:terminal.context,
    core:terminal.core}),1);
  assert.match(terminal.posted[0].body,/WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN/);
});

test('known terminal report prevents later advisory but not via arbitrary links', async () => {
  const active = run({status:'in_progress',conclusion:null});
  const x = fake({event:'schedule', workflowRuns:[active],
    jobs:[{name:'validate',conclusion:'success'},
      {name:'execute',status:'in_progress',started_at:'2026-10-28T06:00:00Z'}],
    comments:[{user:{login:'github-actions[bot]'},
      body:'EF02_OBSERVER run=555 code=WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN'}]});
  assert.equal(await observe({github:x.api,context:x.context,core:x.core},
    {nowMs:Date.parse('2026-10-28T09:10:00Z')}),0);
});
