# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2017-08-23 09:42
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('indicators', '0007_level_color'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='historicalcollecteddata',
            name='history_change_reason',
        ),
        migrations.RemoveField(
            model_name='historicalindicator',
            name='history_change_reason',
        ),
    ]