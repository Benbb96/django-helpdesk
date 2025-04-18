# -*- coding: utf-8 -*-
# Generated by Django 1.10.1 on 2016-09-14 23:47
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("helpdesk", "0012_queue_default_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="queue",
            name="email_box_local_dir",
            field=models.CharField(
                blank=True,
                help_text="If using a local directory, what directory path do you wish to poll for new email? Example: /var/lib/mail/helpdesk/",
                max_length=200,
                null=True,
                verbose_name="E-Mail Local Directory",
            ),
        ),
        migrations.AddField(
            model_name="queue",
            name="logging_dir",
            field=models.CharField(
                blank=True,
                help_text="If logging is enabled, what directory should we use to store log files for this queue? The standard logging mechanims are used if no directory is set",
                max_length=200,
                null=True,
                verbose_name="Logging Directory",
            ),
        ),
        migrations.AddField(
            model_name="queue",
            name="logging_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("none", "None"),
                    ("debug", "Debug"),
                    ("info", "Information"),
                    ("warn", "Warning"),
                    ("error", "Error"),
                    ("crit", "Critical"),
                ],
                help_text="Set the default logging level. All messages at that level or above will be logged to the directory set below. If no level is set, logging will be disabled.",
                max_length=5,
                null=True,
                verbose_name="Logging Type",
            ),
        ),
        migrations.AlterField(
            model_name="queue",
            name="email_box_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pop3", "POP 3"),
                    ("imap", "IMAP"),
                    ("local", "Local Directory"),
                ],
                help_text="E-Mail server type for creating tickets automatically from a mailbox - both POP3 and IMAP are supported, as well as reading from a local directory.",
                max_length=5,
                null=True,
                verbose_name="E-Mail Box Type",
            ),
        ),
    ]
