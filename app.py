import aws_cdk as cdk
from deployment.bot_stack import CoBotStack

import os
import subprocess
import shutil
from pathlib import Path


def deploy():
    app = cdk.App()
    skip_ecs = app.node.try_get_context("skip_ecs")
    pause_ecs = app.node.try_get_context("pause_ecs")
    CoBotStack(app,
               "CoBotStack",
               env=cdk.Environment(account=app.node.try_get_context("account"),
                                   region=app.node.try_get_context("region")
                                   or "us-west-2"),
               skip_ecs=skip_ecs,
               pause_ecs=pause_ecs)

    app.synth()


if __name__ == "__main__":
    deploy()
