'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const observe = require('../.github/scripts/ef02-watch.cjs');

const owner = 'trung-via', repo = 'ai-commerce';
function run(overrides = {}) {
  return {id: 555, name: 'EF02 Multi-Action remote engineering',
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
  assert.equal(observe.issueNumber(run({display_title: 'Unrelated #19'}), 'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({head_repository: {full_name: 'other/fork'}}), 'trung-via/ai-commerce'), null);
  assert.equal(observe.issueNumber(run({display_title: 'EF02 Issue #0'}), 'trung-via/ai-commerce'), null);
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
    body: 'EF02 request REJECTED. Workflow: https://github.com/trung-via/ai-commerce/actions/runs/555'
  }]});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 0);
  assert.equal(x.posted.length, 0);
});

test('successful workflow and unrelated workflows do not produce comments', async () => {
  const x = fake({result: run({conclusion: 'success'})});
  assert.equal(await observe({github: x.api, context: x.context, core: x.core}), 0);
  const y = fake({result: run({name: 'unrelated', conclusion: 'cancelled'})});
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
  const line = workflow.split('\\n').find(x => x.startsWith('run-name: '));
  assert.ok(line, 'workflow should bind Issue number to run title');
  assert.ok(line.startsWith('run-name: "EF02 Issue #'), 'YAML # must be quoted');
  assert.ok(line.endsWith('"'), 'run-name must close its quote');
  assert.ok(line.includes('github.event.issue.number || inputs.issue_number'));
});
