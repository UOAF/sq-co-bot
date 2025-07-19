from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_secretsmanager as sm,
    RemovalPolicy,
    SecretValue,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_iam as iam,
)
from constructs import Construct
import os


class CoBotStack(Stack):

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 skip_ecs=False,
                 pause_ecs=False,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        audio_bucket = s3.Bucket(
            self,
            "AudioBucket",
            bucket_name=f"discord-bot-audio-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        s3deploy.BucketDeployment(
            self,
            "DeployAudio",
            sources=[s3deploy.Source.asset("./sounds")],
            destination_bucket=audio_bucket,
        )

        bot_token_secret = sm.Secret(
            self,
            "BotTokenSecret",
            secret_name="discord-bot-token",
            description="Discord bot authentication token",
            secret_object_value={
                "bot_token": SecretValue.unsafe_plain_text("your-bot-token"),
                "public_key": SecretValue.unsafe_plain_text("your-public-key")
            })

        add_fargate = not skip_ecs
        if add_fargate:
            self._add_fargate_service(audio_bucket, bot_token_secret,
                                      pause_ecs)

        # OIDC provider for GitHub Actions
        oidc_provider = iam.OpenIdConnectProvider(
            self,
            "GitHubOIDCProvider",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"])

        github_policies = [
            "AmazonEC2FullAccess", "AmazonECS_FullAccess",
            "AmazonS3FullAccess", "SecretsManagerReadWrite",
            "CloudWatchLogsFullAccess", "AWSCloudFormationFullAccess",
            "IAMFullAccess"
        ]

        # IAM role for GitHub Actions
        github_actions_role = iam.Role(
            self,
            "GitHubActionsDeployRole",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={
                    "StringLike": {
                        # Replace with your GitHub org/repo and branch as needed
                        "token.actions.githubusercontent.com:sub":
                        "repo:UOAF/sq-co-bot:ref:refs/heads/prod"
                    }
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(p)
                for p in github_policies
            ])

    def _add_fargate_service(self, audio_bucket, bot_token_secret, pause_ecs):
        vpc = ec2.Vpc(self,
                      "BotVPC",
                      max_azs=2,
                      nat_gateways=1,
                      subnet_configuration=[
                          ec2.SubnetConfiguration(
                              name="public",
                              subnet_type=ec2.SubnetType.PUBLIC,
                              cidr_mask=24),
                          ec2.SubnetConfiguration(
                              name="private",
                              subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                              cidr_mask=24),
                      ])

        cluster = ecs.Cluster(self,
                              "VoiceCluster",
                              vpc=vpc,
                              container_insights=True)

        task_definition = ecs.FargateTaskDefinition(
            self,
            "VoiceTaskDef",
            memory_limit_mib=512,
            cpu=256,
        )

        container = task_definition.add_container(
            "voice-bot",
            image=ecs.ContainerImage.from_asset(
                ".", file="voice_container/Dockerfile"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="voice-bot",
                log_retention=logs.RetentionDays.THREE_DAYS),
            environment={
                "AUDIO_BUCKET": audio_bucket.bucket_name,
            },
            secrets={
                "DISCORD_TOKEN":
                ecs.Secret.from_secrets_manager(bot_token_secret,
                                                field="bot_token")
            })

        # Use pause_ecs to set desired_count
        service = ecs.FargateService(self,
                                     "VoiceBotService",
                                     cluster=cluster,
                                     task_definition=task_definition,
                                     desired_count=0 if pause_ecs else 1,
                                     capacity_provider_strategies=[
                                         ecs.CapacityProviderStrategy(
                                             capacity_provider="FARGATE_SPOT",
                                             weight=1,
                                             base=1)
                                     ])

        audio_bucket.grant_read(task_definition.task_role)
        bot_token_secret.grant_read(task_definition.task_role)
