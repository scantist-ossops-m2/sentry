# Generated by Django 3.2.23 on 2023-11-09 20:25

from sentry.new_migrations.migrations import CheckedMigration


class Migration(CheckedMigration):
    # data migration: must be run out of band
    is_post_deployment = True

    # data migration: run outside of a transaction
    atomic = False

    dependencies = [
        ("sentry", "0595_trivial_but_dangerous_3"),
    ]

    operations = []
