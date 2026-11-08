import { REPO_URL, SURVEY_URL } from "../env";
import { Mark } from "./Mark";
import { SignInLink } from "./SignInLink";

export function Why() {
  return (
    <section className="why wrap" id="why" aria-labelledby="why-title">
      <div className="split">
        <h2 className="section-title" id="why-title">
          Why not just revoke it?
        </h2>
        <div className="split-body">
          <p className="lead">
            Revoking stops the agent&rsquo;s next call. It does nothing about
            the keys already sitting in its context, its logs, or an
            attacker&rsquo;s hands. The safe fallback is rotating every key,
            which stops every agent at once.
          </p>
          <p className="body-lg">
            Burn keeps a ledger of which instance saw which key. So it rotates
            only the keys that instance owned, flags for review any shared key
            whose value it saw, and stops only the instance with evidence
            against it.
          </p>
          <p className="why-note">
            A burn revokes the instance first, so the Gateway refuses its next
            tool call even before its keys are rotated.
          </p>
          <p className="fine">
            In a 2025 SailPoint survey of security and IT professionals and
            executives, 82% of organizations already used AI agents, but only
            44% reported having policies in place to secure them, and 23%
            reported their AI agents had been tricked into revealing access
            credentials. <a href={SURVEY_URL}>Read the report</a>
          </p>
        </div>
      </div>
    </section>
  );
}

const SERVICES: readonly [name: string, job: string][] = [
  [
    "Amazon Bedrock AgentCore Gateway",
    "Every tool call passes through it, with Burn’s checks on the way in and out.",
  ],
  [
    "AWS Lambda",
    "Runs those checks on each request and response, the Burn API, and the minter that issues keys.",
  ],
  [
    "Amazon API Gateway",
    "Fronts the Burn API and the mock provider as HTTP APIs. With people sign-in on, a JWT authorizer checks each person’s token first.",
  ],
  [
    "Amazon Cognito",
    "Issues each agent instance its own identity, and signs people in.",
  ],
  [
    "AWS IAM",
    "Issues an instance’s own AWS keys, to a user inside a permissions boundary the minter can’t remove.",
  ],
  [
    "Amazon DynamoDB",
    "Holds the exposure ledger, as fingerprints rather than values, and the event log.",
  ],
  [
    "AWS Secrets Manager",
    "Holds every key Burn mints, shared keys and decoys included. A burn writes the replacements here.",
  ],
  [
    "AWS CloudTrail",
    "Shows what a burned instance’s AWS keys did, from the account’s event history.",
  ],
];

export function Aws() {
  return (
    <section className="block wrap" id="aws" aria-labelledby="aws-title">
      <div className="aws-head block-rule">
        <h2 className="section-title" id="aws-title">
          What each AWS service does
        </h2>
        <p>
          Each Lambda runs with its own role, scoped to the tables and secrets
          it uses. No execution role has wildcard actions.
        </p>
      </div>
      <dl className="aws-grid">
        {SERVICES.map(([name, job]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd>{job}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function Closing() {
  return (
    <section className="closing wrap" aria-labelledby="closing-title">
      <div className="block-rule closing-rule">
        <h2 className="closing-title" id="closing-title">
          Burn one agent, not the whole swarm.
        </h2>
        <div className="closing-actions">
          <SignInLink className="btn btn-dark btn-lg" />
          <a className="btn btn-lg" href={REPO_URL}>
            Read the code
          </a>
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-inner">
        <Mark size="sm" />
        <p>Built at the AWS &times; A10 Hack, November 2026.</p>
        <a href={REPO_URL}>GitHub</a>
      </div>
    </footer>
  );
}
