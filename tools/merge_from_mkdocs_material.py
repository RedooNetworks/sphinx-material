#!/usr/bin/env python3

import argparse
import contextlib
import os
import subprocess
import tempfile

MKDOCS_EXCLUDE_PATTERNS = [
    # mkdocs-specific configuration files
    '.gitignore',
    '.gitattributes',
    '.github',
    '.browserslistrc',
    '.dockerignore',
    'requirements.txt',
    'setup.py',
    'Dockerfile',
    'MANIFEST.in',

    # Generated files
    'material',

    # mkdocs-specific files
    'src/*.py',
    'src/mkdocs_theme.yml',
    'src/404.html',
    'mkdocs.yml',

    # Unneeded files
    'typings/lunr',
    'src/assets/javascripts/browser/worker',
    'src/assets/javascripts/integrations/search/worker',

    # Files specific to mkdocs' own documentation
    'src/overrides',
    'src/assets/images/favicon.png',
    'src/.icons/logo.*',
    'docs',
    'LICENSE',
    'CHANGELOG',
    'package-lock.json',
    '*.md',
]

ap = argparse.ArgumentParser()
ap.add_argument('--source-ref', type=str, default='mkdocs-material/master')
ap.add_argument('--keep-temp',
                action='store_true',
                help='Keep temporary workdir')
ap.add_argument('--dry-run',
                action='store_true',
                help='Just print the merge parent.')
args = ap.parse_args()
source_ref = args.source_ref

script_dir = os.path.abspath(os.path.dirname(__file__))

# Determine previous "Pull non-excluded files" commit.
branch_point_result = subprocess.run(['git', 'merge-base', 'HEAD', source_ref],
                                     stdout=subprocess.PIPE,
                                     text=True)
if branch_point_result.returncode == 0:
    prev_merge_commit = subprocess.run(
        [
            'git', 'rev-list', 'HEAD',
            '^%s' % branch_point_result.stdout.strip(), '--ancestry-path',
            '--reverse'
        ],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.splitlines()[0]
else:
    prev_merge_commit = None
    print('No previous merge found')


@contextlib.contextmanager
def _temp_worktree_path():
    if args.keep_temp:
        temp_workdir = tempfile.mkdtemp()
        yield temp_workdir
        return
    with tempfile.TemporaryDirectory() as temp_workdir:
        try:
            yield temp_workdir
        finally:
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', temp_workdir],
                check=True,
            )


with _temp_worktree_path() as temp_workdir:
    print(f'Checking out {source_ref} -> {temp_workdir}')
    subprocess.run(
        ['git', 'worktree', 'add', '--detach', temp_workdir, source_ref],
        check=True,
    )
    if prev_merge_commit is not None:
        # Also add previous "Pull non-excluded files" commit as a merge
        # parent.  This avoids spurious merge conflicts due to the
        # deletions.
        print('Adding merge parent')
        subprocess.run(
            [
                'git', 'merge', '-s', 'ours', '--no-ff', '--no-commit',
                prev_merge_commit
            ],
            cwd=temp_workdir,
            check=True,
        )
    print('Removing excluded files')
    subprocess.run(
        ['git', 'rm', '--quiet', '-r'] + MKDOCS_EXCLUDE_PATTERNS,
        cwd=temp_workdir,
        check=True,
    )
    print('Performing merge')
    print('You will have to commit manually once any conflicts are resolved')
    subprocess.run(
        ['git', 'commit', '-m', f'Pull non-excluded files from {source_ref}'],
        cwd=temp_workdir,
        check=True,
    )
    parent_commit = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=temp_workdir,
        text=True,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if args.dry_run:
        print(parent_commit)
    else:
        commit_msg = f'Merge changes from mkdocs-material'
        extra_args = []
        if prev_merge_commit is None:
            commit_msg = '''Pull in pristine files from mkdocs-material

This just pulls in the unmodified files from mkdocs-material to serve
as a merge base.  Subsequent commits will make necessary modifications
to these files, and integrate them into the sphinx theme.
'''
            extra_args.append('--allow-unrelated-histories')
        else:
            extra_args.append('--no-commit')
            extra_args.append('--log')
        subprocess.run(
            [
                'git',
                'merge',
                '--no-ff',
                '--no-verify',
                '-m',
                commit_msg,
                parent_commit,
            ] + extra_args,
            check=True,
        )
