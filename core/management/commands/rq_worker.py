"""
Custom RQ worker command that respects Django RQ_QUEUES configuration.
"""

import os
import django
from django.core.management.base import BaseCommand
from django.conf import settings
from rq import Worker, Queue
import redis
import logging

logger = logging.getLogger('django')

# Ensure Django is set up
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mlva.settings')
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    raise RuntimeError('DJANGO_SETTINGS_MODULE must be set')


class Command(BaseCommand):
    help = 'Starts an RQ worker with proper Django configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            'queues',
            nargs='*',
            default=['default'],
            help='Queue names to listen on (default: default)'
        )

    def handle(self, *args, **options):
        # Ensure Django is fully initialized
        django.setup()
        
        queue_names = options['queues']
        
        self.stdout.write(
            self.style.SUCCESS(f'=== Starting RQ Worker ===')
        )
        self.stdout.write(
            self.style.SUCCESS(f'Listening on queues: {", ".join(queue_names)}')
        )
        
        # Get queue objects from Django RQ_QUEUES configuration
        queues = []
        redis_conns = {}
        
        for queue_name in queue_names:
            if queue_name in settings.RQ_QUEUES:
                config = settings.RQ_QUEUES[queue_name]
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Configuring queue "{queue_name}": {config["HOST"]}:{config["PORT"]} DB {config["DB"]}'
                    )
                )
                
                redis_conn = redis.Redis(
                    host=config['HOST'],
                    port=config['PORT'],
                    db=config['DB']
                    # Do NOT use decode_responses=True - RQ handles its own serialization
                )
                
                # Test connection
                try:
                    redis_conn.ping()
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Redis connection successful for "{queue_name}"')
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'✗ Redis connection failed for "{queue_name}": {str(e)}')
                    )
                    continue
                
                redis_conns[queue_name] = redis_conn
                queues.append(Queue(queue_name, connection=redis_conn))
            else:
                self.stdout.write(
                    self.style.ERROR(f'✗ Queue "{queue_name}" not found in RQ_QUEUES')
                )
                continue
        
        if not queues:
            self.stdout.write(self.style.ERROR('✗ No queues to listen on. Exiting.'))
            return
        
        # Clean up stale worker registrations from previous runs
        try:
            from rq.worker import Worker as RQWorker
            for queue_name, redis_conn in redis_conns.items():
                all_workers = RQWorker.all(connection=redis_conn)
                for worker in all_workers:
                    if worker.name == 'mlva-worker':
                        try:
                            worker.clean_registries()
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Cleaned stale worker registration: {worker.name}'
                                )
                            )
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(f'Could not clean stale worker: {str(e)}')
                            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clean stale workers: {str(e)}')
            )
        
        # Start the worker
        self.stdout.write(
            self.style.SUCCESS(f'✓ RQ worker starting on {len(queues)} queue(s)')
        )
        self.stdout.write(
            self.style.SUCCESS(f'=== Worker ready and listening for jobs ===\n')
        )
        
        worker = Worker(queues, name='mlva-worker', disable_default_exception_handler=False)
        worker.work(with_scheduler=True)
