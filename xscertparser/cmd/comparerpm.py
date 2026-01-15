#!/usr/bin/env python3

import sys
import os
import re
import subprocess
import tarfile
import tempfile
import time
import shutil
import urllib.request


def show_help():
    """
    Display help message for the script.
    """
    help_text = """
Usage: ./comparerpm.py <input_file> <repo_url> [<repo_url> ...]

Compares RPM packages in a local file/archive against one or more YUM/DNF repos.
Reports packages missing from each repo or with newer local versions.

Arguments:
    <input_file>    rpm-qa.out file or ack-submission.tar.gz archive
    <repo_url>      YUM/DNF repo URL (can specify multiple URLs)


Examples:
    python3 comparerpm.py rpm-qa.out https://repo.ops.xenserver.com/xs8/earlyaccess
    python3 comparerpm.py ack-submission.tar.gz https://repo.ops.xenserver.com/xs8/earlyaccess
    python3 comparerpm.py ack-submission.tar.gz https://repo.ops.xenserver.com/xs8/earlyaccess https://repo.ops.xenserver.com/xs8/base

Output:
    A text file rpm-compare-result-<timestamp>.txt for the comparing results, containing:
    - Packages not found in repos
    - Packages with higher version-release than in repos
    """
    print(help_text)


def split_rpmver(ver):
    '''
    Split the version string into a sequence of numeric and alphabetic segments.
    For example: "1.2a-3" -> ['1', '.', '2', 'a', '-', '3']
    '''
    return re.findall(r'([0-9]+|[a-zA-Z]+|\.|\-)', ver)

def char_type_order(x):
    return (x.isdigit(), x.isalpha())

def vercmp(a, b) -> int:
    pa = split_rpmver(a)
    pb = split_rpmver(b)
    while pa or pb:
        if pa:
            a_elem = pa.pop(0)
        else:
            a_elem = ''
        if pb:
            b_elem = pb.pop(0)
        else:
            b_elem = ''
        if a_elem == b_elem:
            continue
        if b_elem == '':
            return 1
        if a_elem == '':
            return -1
        # Compare as numbers if both are numeric
        if a_elem.isdigit() and b_elem.isdigit():
            a_num = int(a_elem.lstrip('0') or '0')
            b_num = int(b_elem.lstrip('0') or '0')
            if a_num != b_num:
                return (a_num > b_num) - (a_num < b_num)
            continue
        # Compare as strings if both are alphabetic
        if a_elem.isalpha() and b_elem.isalpha():
            if a_elem != b_elem:
                return (a_elem > b_elem) - (a_elem < b_elem)
            continue
        # Otherwise, numeric > alphabetic, other symbols lowest priority
        return (char_type_order(a_elem) > char_type_order(b_elem)) - (char_type_order(a_elem) < char_type_order(b_elem))
    return 0

def rpmvercmp(ver1, rel1, ver2, rel2) -> int:
    """
    Version and release comparison
    Returns 1 if ver1-rel1 > ver2-rel2, -1 if less, 0 if equal
    """
    cmpres = vercmp(ver1, ver2)
    if cmpres == 0:
        cmpres = vercmp(rel1, rel2)
    return cmpres

def parse_rpm(rpm):
    """
    Extract RPM name, version, and release from the RPM filename.
    Example: wget-1.21.3-2.xs8.x86_64
    Returns 'wget', '1.21.3', '2.xs8'
    """
    m = re.match(r'(.+)-([^-]+)-([^-]+)\.[^.]+$', rpm)
    if not m:
        raise ValueError(f"Failed to parse RPM filename: {rpm}")
    name, version, release = m.group(1), m.group(2), m.group(3)
    return name, version, release

def validate_repo_url(repo_url):
    """
    Validate that the repo URL is valid.
    """
    try:
        repomd_url = repo_url.rstrip('/') + '/repodata/repomd.xml'
        with urllib.request.urlopen(repomd_url, timeout=5):
            pass
    except Exception:
        print(f"Error: repo URL is not a valid repo: {repomd_url}")
        sys.exit(2)

def generate_cmd_for_repoquery(repo_urls):
    """
    Generate the dnf repoquery command for multiple repos
    Use 'dnf' if available, otherwise use 'repoquery'
    """
    if shutil.which("dnf"):
        cmd = ["dnf"]
        for i, repo_url in enumerate(repo_urls):
            validate_repo_url(repo_url)
            cmd += [f"--repofrompath=myrepo{i},{repo_url}", f"--repo=myrepo{i}"]
        cmd += ["repoquery", "--qf", "%{name}-%{version}-%{release}.%{arch}\n", "--latest-limit=1"]
    else:
        cmd = ["repoquery", "--disablerepo='*'"]
        for i, repo_url in enumerate(repo_urls):
            validate_repo_url(repo_url)
            cmd += [f"--repofrompath=myrepo{i},{repo_url}", f"--enablerepo=myrepo{i}"]
        cmd += ["-a", "--qf", "%{name}-%{version}-%{release}.%{arch}"]
    return cmd

def parse_repo_urls(repo_urls):
    """
    Parse multiple repos and combine the results, keeping highest version-release per package
    """
    rpms = {}
    cmd = generate_cmd_for_repoquery(repo_urls)
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', check=True, timeout=10)
        rpm_lines = result.stdout.strip().splitlines()
        for line in rpm_lines:
            rpm = line.strip()
            if not rpm:  # skip empty lines
                continue
            name, version, release = parse_rpm(rpm)
            repo_info = {'rpm': rpm, 'version': version, 'release': release}
            if name not in rpms:
                rpms[name] = repo_info
            else:
                existing = rpms[name]
                cmp_res = rpmvercmp(
                    repo_info['version'], repo_info['release'],
                    existing['version'], existing['release']
                )
                if cmp_res > 0:
                    rpms[name] = repo_info
        return rpms
    except Exception as e:
        raise RuntimeError(f"Failed to query repo. Ensure that YUM/DNF is installed and the repo URLs are valid YUM/DNF repos. Details: {e}")

def parse_rpm_qa_file(rpm_qa_file):
    """
    Parse rpm-qa.out file and return a dictionary of <name, version-release>
    """
    rpms = {}
    if not os.path.exists(rpm_qa_file):
        print(f"Error: file does not exist: {rpm_qa_file}")
        sys.exit(2)
    with open(rpm_qa_file) as f:
        for line in f:
            rpm = line.strip()
            if not rpm:
                continue
            try:
                name, version, release = parse_rpm(rpm)
                rpms[name] = {'rpm': rpm, 'version': version, 'release': release}
            except Exception:
                pass
    return rpms

def parse_tar_gz_file(tar_gz_file):
    """
    Extract rpm-qa.out from nested tar.bz2 inside a tar.gz archive.
    Parse the rpm-qa.out file and return a dictionary of <name, version-release>
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(tar_gz_file, 'r:gz') as tar:
            # Only extract tar.bz2 files
            found_bz2 = False
            for member in tar.getmembers():
                if "bug-report" in member.name and member.name.endswith('.tar.bz2'):
                    tar.extract(member, tmpdir)
                    tar_bz2_path = os.path.join(tmpdir, member.name)
                    found_bz2 = True
                    break
            if not found_bz2:
                raise FileNotFoundError("No tar.bz2 bug-report file found in tar.gz archive")

            with tarfile.open(tar_bz2_path, 'r:bz2') as tar_bz2:
                # Only extract rpm-qa.out from tar.bz2
                for member in tar_bz2.getmembers():
                    if member.name.endswith('rpm-qa.out'):
                        tar_bz2.extract(member, tmpdir)
                        rpm_qa_path = os.path.join(tmpdir, member.name)
                        return parse_rpm_qa_file(rpm_qa_path)
                raise FileNotFoundError("No rpm-qa.out file found in tar.bz2 archive")

def compare_rpms(rpm_dict1, rpm_dict2):
    """
    Compare two RPM dictionaries and return lists of RPMs only in rpm_dict1 and those with higher versions.
    """
    only_in_file = []
    higher_in_file = []

    for name, rpm_info1 in rpm_dict1.items():
        rpm_full = rpm_info1['rpm']
        version1 = rpm_info1['version']
        release1 = rpm_info1['release']
        if name not in rpm_dict2:
            only_in_file.append(rpm_full)
        else:
            rpm_info2 = rpm_dict2[name]
            cmp_res = rpmvercmp(version1, release1, rpm_info2['version'], rpm_info2['release'])
            if cmp_res > 0:
                higher_in_file.append(rpm_full)

    return only_in_file, higher_in_file

def save_output(only_in_file, higher_in_file):
    """
    Save comparison results to a file.
    """
    
    timestamp = int(time.time())
    result_file = f"rpm-compare-result-{timestamp}.txt"
    with open(result_file, 'w') as f:
        f.write("RPMs in rpm-qa.out that do NOT exist in any repo:\n")
        for rpm in only_in_file:
            f.write(rpm + "\n")
        f.write("\nRPMs in rpm-qa.out with higher version-release than all repos:\n")
        for rpm in higher_in_file:
            f.write(rpm + "\n")
    print(f"Found {len(only_in_file)} packages not in repos, {len(higher_in_file)} packages with higher versions")
    print(f"Result saved to {result_file}")

def main():
    # need at least: script input_file repo1
    if len(sys.argv) < 3:
        show_help()
        sys.exit(1)

    input_file = sys.argv[1]
    repo_urls = sys.argv[2:]

    print(f"Parsing input file: {input_file}")
    if input_file.endswith('.out'):
        rpm_dict1 = parse_rpm_qa_file(input_file)
    elif input_file.endswith('.tar.gz'):
        rpm_dict1 = parse_tar_gz_file(input_file)
    else:
        print("Error: input_file must have .out or .tar.gz extension")
        sys.exit(1)

    print(f"Parsing repo URLs: {', '.join(repo_urls)}")
    rpm_dict2 = parse_repo_urls(repo_urls)

    print(f"Comparing RPMs...")
    only_in_file, higher_in_file = compare_rpms(rpm_dict1, rpm_dict2)
    save_output(sorted(only_in_file), sorted(higher_in_file))

if __name__ == "__main__":
    main()
