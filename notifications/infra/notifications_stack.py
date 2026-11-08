"""BurnNotifications: PROPOSED stack for the infra owner. Not wired into infra/app.py.

    burn-events DynamoDB stream (NEW_IMAGE)          <- needs the one-line change in docs/infra-integration.md
      -> EventBridge Pipe (filter: eventName = INSERT)
      -> SQS burn-notify (SSE, redrive to burn-notify-dlq after 5 receives)
      -> Lambda burn-notify (ReportBatchItemFailures) -> Slack / Telegram / Discord / Teams / SES
    EventBridge schedule (1 min) -> Lambda burn-notify-flush (aggregate summaries, digests)
    DynamoDB burn-notify-state (delivery claims + history, TTL)

Context values (all optional except the stream ARN):
    burn:events_stream_arn    stream ARN of burn-events (after the stream is enabled)
    burn:notify_config        path to the notifications config JSON (secret_refs only, no secrets)
    burn:notify_ses_identity  verified SES identity name (domain or address) the Lambda may send from
    burn:dashboard_origin     https origin of the hosted dashboard (links go to <origin>/#/alerts)

Least privilege: the Pipe role can read only the burn-events stream and send only to
the queue; the Lambda role can read only secrets named burn-notify/*, use only its
own state table, and send email only from the one SES identity. Nothing here can
write to any BURN table.
"""
from __future__ import annotations

import json
import pathlib

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_pipes as pipes
from aws_cdk import aws_sqs as sqs
from constructs import Construct

NOTIFICATIONS = pathlib.Path(__file__).resolve().parents[1]
MAX_RECEIVES = 5
LAMBDA_TIMEOUT = 30
STATE_TABLE = "burn-notify-state"
SECRET_PREFIX = "burn-notify/"
CODE_EXCLUDE = ["tests", "infra", "docs", ".venv", ".pytest_cache", "**/__pycache__", "*.md", "uv.lock",
                "pyproject.toml", "*.json"]


class BurnNotificationsStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ctx = self.node.try_get_context
        stream_arn = ctx("burn:events_stream_arn")
        if not stream_arn:
            raise ValueError("pass -c burn:events_stream_arn=<burn-events stream ARN> (enable the stream first)")
        config_path = ctx("burn:notify_config")
        config_json = json.dumps(json.loads(pathlib.Path(config_path).read_text())) if config_path else \
            json.dumps({"destinations": []})

        # --- state ------------------------------------------------------------------------
        state = ddb.Table(
            self, "State", table_name=STATE_TABLE,
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, time_to_live_attribute="ttl",
            removal_policy=cdk.RemovalPolicy.DESTROY)
        state.add_global_secondary_index(
            index_name="due", partition_key=ddb.Attribute(name="due", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="window_end", type=ddb.AttributeType.NUMBER))

        # --- queues -----------------------------------------------------------------------
        dlq = sqs.Queue(self, "Dlq", queue_name="burn-notify-dlq", retention_period=cdk.Duration.days(14),
                        encryption=sqs.QueueEncryption.SQS_MANAGED, enforce_ssl=True)
        queue = sqs.Queue(
            self, "Queue", queue_name="burn-notify", encryption=sqs.QueueEncryption.SQS_MANAGED, enforce_ssl=True,
            visibility_timeout=cdk.Duration.seconds(LAMBDA_TIMEOUT * 6),
            retention_period=cdk.Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=MAX_RECEIVES))

        # --- stream -> queue (EventBridge Pipe) -------------------------------------------
        pipe_role = iam.Role(self, "PipeRole", assumed_by=iam.ServicePrincipal("pipes.amazonaws.com"))
        pipe_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"],
            resources=[stream_arn]))
        pipe_role.add_to_policy(iam.PolicyStatement(actions=["dynamodb:ListStreams"], resources=["*"]))
        queue.grant_send_messages(pipe_role)
        pipes.CfnPipe(
            self, "EventsToQueue", name="burn-events-to-notify", role_arn=pipe_role.role_arn,
            source=stream_arn, target=queue.queue_arn,
            source_parameters=pipes.CfnPipe.PipeSourceParametersProperty(
                dynamo_db_stream_parameters=pipes.CfnPipe.PipeSourceDynamoDBStreamParametersProperty(
                    starting_position="LATEST", batch_size=10, maximum_batching_window_in_seconds=1,
                    maximum_retry_attempts=10),
                filter_criteria=pipes.CfnPipe.FilterCriteriaProperty(filters=[
                    pipes.CfnPipe.FilterProperty(pattern=json.dumps({"eventName": ["INSERT"]}))])))

        # --- Lambdas ----------------------------------------------------------------------
        env = {"BURN_NOTIFY_STATE_TABLE": STATE_TABLE, "BURN_NOTIFY_CONFIG_JSON": config_json,
               "BURN_NOTIFY_MAX_RECEIVES": str(MAX_RECEIVES), "BURN_NOTIFY_ALLOW_ENV_SECRETS": "0",
               "BURN_NOTIFY_METRICS": "1"}
        if ctx("burn:dashboard_origin"):
            env["BURN_DASHBOARD_ORIGIN"] = ctx("burn:dashboard_origin")
        code = lambda_.Code.from_asset(str(NOTIFICATIONS), exclude=CODE_EXCLUDE)

        def fn(name: str, handler: str) -> lambda_.Function:
            f = lambda_.Function(
                self, name, function_name=name.lower().replace("notifyflush", "notify-flush"),
                runtime=lambda_.Runtime.PYTHON_3_12, handler=handler, code=code,
                timeout=cdk.Duration.seconds(LAMBDA_TIMEOUT), memory_size=256, environment=env,
                reserved_concurrent_executions=5)
            state.grant_read_write_data(f)
            f.add_to_role_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:{cdk.Aws.PARTITION}:secretsmanager:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
                           f"secret:{SECRET_PREFIX}*"]))
            identity = ctx("burn:notify_ses_identity")
            if identity:
                f.add_to_role_policy(iam.PolicyStatement(
                    actions=["ses:SendEmail"],
                    resources=[f"arn:{cdk.Aws.PARTITION}:ses:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:identity/{identity}"]))
            return f

        worker = fn("Burn-Notify", "burn_notify.handler.queue_handler")
        worker.add_event_source(sources.SqsEventSource(
            queue, batch_size=10, max_batching_window=cdk.Duration.seconds(1), report_batch_item_failures=True))
        queue.grant_consume_messages(worker)  # includes ChangeMessageVisibility for Retry-After

        flush = fn("Burn-NotifyFlush", "burn_notify.handler.flush_handler")
        events.Rule(self, "FlushSchedule", schedule=events.Schedule.rate(cdk.Duration.minutes(1)),
                    targets=[targets.LambdaFunction(flush)])

        # --- alarms -----------------------------------------------------------------------
        cw.Alarm(self, "DlqNotEmpty", alarm_name="burn-notify-dlq-not-empty",
                 metric=dlq.metric_approximate_number_of_messages_visible(period=cdk.Duration.minutes(1)),
                 threshold=0, comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                 evaluation_periods=1, treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
        cw.Alarm(self, "WorkerErrors", alarm_name="burn-notify-worker-errors",
                 metric=worker.metric_errors(period=cdk.Duration.minutes(5)), threshold=0,
                 comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD, evaluation_periods=1,
                 treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
        cw.Alarm(self, "QueueAge", alarm_name="burn-notify-queue-age",
                 metric=queue.metric_approximate_age_of_oldest_message(period=cdk.Duration.minutes(1)),
                 threshold=300, comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                 evaluation_periods=3, treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
