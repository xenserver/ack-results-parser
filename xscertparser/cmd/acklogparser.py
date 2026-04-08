#!/usr/bin/env python3

"""Entry point script for parsing specified log files"""
from argparse import ArgumentParser
from xscertparser.utils import extract_file_from_tar
from xscertparser import xmltojson
import os
import re
import subprocess
import urllib.request
import xml.dom.minidom
import pprint
from hwinfo.tools import inspector
import tempfile
import shutil
import tarfile
from pymongo import MongoClient
# import models

NICS_DICT = {}
HBAS_DICT = {}
MACHINE_DICT = {"pass": [], "fail": []}  # seprates server Products PASS/FAIL
FAILED_DICT = {}
SERVER_DICT = {
    'xs_version': u'None',
    'system-manufacturer': 'None',
    'sockets': 'None',
    'product': 'None',
    'chassis': 'None',
    'modelname': 'None',
    'family': 'None',
    'model': 'None',
    'stepping': 'None',
    'nics': [],
    'hbas': [],
    }
DRIVER_BLACK_LIST = ["qla4xxx", "qla3xxx", "netxen_nic", "qlge", "qlcnic"]
IGNORED_RPM_NAMES = {
    'auto-cert-kit',
    'control-auto-cert-kit',
    'update-auto-cert-kit',
    'gpg-pubkey',
}
REPO_CONFIG = {
    '8.4': [
        'https://repo.ops.xenserver.com/xs8/base',
        'https://repo.ops.xenserver.com/xs8/normal',
        'https://repo.ops.xenserver.com/xs8/earlyaccess'
    ],
    '9': [
        'https://repo.ops.xenserver.com/xs9/base',
        'https://repo.ops.xenserver.com/xs9/normal',
        'https://repo.ops.xenserver.com/xs9/earlyaccess'
    ]
}
DEFAULT_ACK_RPMS = {
   "8.4": {
    "python3-prettytable": {
       "rpm": "python3-prettytable-0.7.2-14.xs8.noarch",
       "version": "0.7.2",
       "release": "xs8"
     },
     "python-hwinfo": {
       "rpm": "python-hwinfo-0.1.7-3.xs8.x86_64",
       "version": "0.1.7",
       "release": "xs8"
     },
     "iperf": {
       "rpm": "iperf-2.0.10-1.el7.x86_64",
       "version": "2.0.10",
       "release": "el7"
     }
   },
   "9": {
     "tcl": {
       "rpm": "tcl-8.6.12-2.xs9.x86_64",
       "version": "8.6.12",
       "release": "xs9"
     },
     "expect": {
       "rpm": "expect-5.45.4-2.xs9.x86_64",
       "version": "5.45.4",
       "release": "xs9"
     },
     "python3-prettytable": {
       "rpm": "python3-prettytable-0.7.2-2.xs9.noarch",
       "version": "0.7.2",
       "release": "xs9"
     },
     "python-hwinfo": {
       "rpm": "python-hwinfo-0.1.12-1.xs9.noarch",
       "version": "0.1.12",
       "release": "xs9"
     },
     "iperf": {
       "rpm": "iperf-2.0.10-1.el7.x86_64",
       "version": "2.0.10",
       "release": "el7"
     }
   }
 }


def vercmp(a, b) -> int:
    def _to_int_tuple(v):
        v = (v or '').strip()
        if not v:
            return ()
        return tuple(int(x or '0') for x in v.split('.'))

    if a==b:
        return 0
    ta = _to_int_tuple(a)
    tb = _to_int_tuple(b)
    max_len = max(len(ta), len(tb))
    ta = ta + (0,) * (max_len - len(ta))
    tb = tb + (0,) * (max_len - len(tb))
    return (ta > tb) - (ta < tb)


def parse_rpm(rpm):
    rpm_noarch = rpm.rsplit('.', 1)[0]
    parts = rpm_noarch.rsplit('-', 2)
    if len(parts) != 3:
        raise ValueError("Failed to parse RPM filename: %s" % rpm)
    name, version, release_full = parts[0], parts[1], parts[2]
    release_tag = release_full.rsplit('.', 1)[-1]
    return name, version, release_tag


def parse_rpm_qa_file(rpm_qa_file):
    rpms = {}
    if not os.path.exists(rpm_qa_file):
        raise FileNotFoundError("Error: file does not exist: %s" % rpm_qa_file)
    with open(rpm_qa_file) as f:
        for line in f:
            rpm = line.strip()
            if not rpm:
                continue
            try:
                name, version, release = parse_rpm(rpm)
                if name in IGNORED_RPM_NAMES:
                    continue
                rpms[name] = {'rpm': rpm, 'version': version, 'release': release}
            except Exception:
                pass
    return rpms


def validate_repo_url(repo_url):
    try:
        repomd_url = repo_url.rstrip('/') + '/repodata/repomd.xml'
        with urllib.request.urlopen(repomd_url, timeout=5):
            pass
    except Exception:
        raise RuntimeError("Error: repo URL is not a valid repo: %s" % repomd_url)


def generate_cmd_for_repoquery(repo_urls):
    if shutil.which("dnf"):
        cmd = ["dnf"]
        for i, repo_url in enumerate(repo_urls):
            validate_repo_url(repo_url)
            cmd += ["--repofrompath=myrepo%d,%s" % (i, repo_url), "--repo=myrepo%d" % i]
        cmd += ["repoquery", "--qf", "%{name}-%{version}-%{release}.%{arch}\n", "--latest-limit=1"]
    else:
        cmd = ["repoquery", "--disablerepo='*'"]
        for i, repo_url in enumerate(repo_urls):
            validate_repo_url(repo_url)
            cmd += ["--repofrompath=myrepo%d,%s" % (i, repo_url), "--enablerepo=myrepo%d" % i]
        cmd += ["-a", "--qf", "%{name}-%{version}-%{release}.%{arch}"]
    return cmd


def parse_repo_urls(repo_urls):
    rpms = {}
    cmd = generate_cmd_for_repoquery(repo_urls)
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', check=True, timeout=30) 
        rpm_lines = result.stdout.strip().splitlines()
        for line in rpm_lines:
            rpm = line.strip()
            if not rpm:
                continue
            name, version, release = parse_rpm(rpm)
            repo_info = {'rpm': rpm, 'version': version, 'release': release}
            if name not in rpms:
                rpms[name] = repo_info
            else:
                existing = rpms[name]
                if repo_info['release'] == existing['release']:
                    cmp_res = vercmp(repo_info['version'], existing['version'])
                    if cmp_res > 0:
                        rpms[name] = repo_info
        return rpms
    except Exception as e:
        raise RuntimeError("Failed to query repo. Ensure that YUM/DNF is installed and repo URLs are valid. Details: %s" % e)


def compare_rpms(rpm_dict1, rpm_dict2):
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
            if rpm_full == rpm_info2['rpm']:
                continue
            if release1 != rpm_info2['release']:
                only_in_file.append(rpm_full)
            else:
                cmp_res = vercmp(version1, rpm_info2['version'])
                if cmp_res > 0:
                    higher_in_file.append(rpm_full)
    return only_in_file, higher_in_file


def parse_xensource_inventory(inventory_path):
    try:
        with open(inventory_path, 'r') as f:
            for line in f:
                if line.startswith('PRODUCT_VERSION_TEXT='):
                    return line.split('=', 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return None


def extract_rpm_qa_and_inventory_from_submission(tar_gz_file):
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(tar_gz_file, 'r:gz') as tar:
            found_bz2 = False
            for member in tar.getmembers():
                if "bug-report" in member.name and member.name.endswith('.tar.bz2'):
                    tar.extract(member, tmpdir)
                    tar_bz2_path = os.path.join(tmpdir, member.name)
                    found_bz2 = True
                    break
            if not found_bz2:
                raise FileNotFoundError("No tar.bz2 bug-report file found in tar.gz archive")

        rpm_qa_dict = None
        product_version = None
        with tarfile.open(tar_bz2_path, 'r:bz2') as tar_bz2:
            for member in tar_bz2.getmembers():
                if member.name.endswith('rpm-qa.out'):
                    tar_bz2.extract(member, tmpdir)
                    rpm_qa_path = os.path.join(tmpdir, member.name)
                    rpm_qa_dict = parse_rpm_qa_file(rpm_qa_path)
                if member.name.endswith('xensource-inventory'):
                    tar_bz2.extract(member, tmpdir)
                    inventory_path = os.path.join(tmpdir, member.name)
                    product_version = parse_xensource_inventory(inventory_path)

        if rpm_qa_dict is None:
            raise FileNotFoundError("No rpm-qa.out file found in bug-report tar.bz2")
        return rpm_qa_dict, product_version


def compare_submission_rpms_with_repos(tar_gz_file):
    rpm_dict1, product_version = extract_rpm_qa_and_inventory_from_submission(tar_gz_file)
    repo_urls = REPO_CONFIG[product_version]
    rpm_dict2 = parse_repo_urls(repo_urls)
    rpm_dict2.update(DEFAULT_ACK_RPMS.get(product_version, {}))
    only_in_file, higher_in_file = compare_rpms(rpm_dict1, rpm_dict2)
    print("\nFound %d packages not in repos, %d packages with higher versions\n" % (len(only_in_file), len(higher_in_file)))
    if only_in_file:
        print("RPMs in rpm-qa.out that do NOT exist in any repo:\n")
        for rpm in only_in_file:
            print(rpm)
    if higher_in_file:
        print("RPMs in rpm-qa.out with higher version-release than all repos:\n")
        for rpm in higher_in_file:
            print(rpm)


def result_parser(tarfilename, logsubdir):  # pylint: disable=R0914,R0912
    """Parse a specified log archive"""
    bugtool_path = extract_file_from_tar(tarfilepath=tarfilename,
                                         fpath="bug-report",
                                         dest=logsubdir,
                                         fullpathknown=False)
    testconf_path = extract_file_from_tar(tarfilename,
                                          'test_run.conf',
                                          logsubdir,
                                          fullpathknown=False)
    dmidecode_path = extract_file_from_tar(
        os.path.join(logsubdir, bugtool_path),
        'dmidecode.out',
        logsubdir,
        False)
    # xapi_db_path = extract_from_tar_with_bname (os.path.join(logsubdir,
    # --> bugtools_path), 'xapi-db.xml', logsubdir)
    lspcivv_path = extract_file_from_tar(
        os.path.join(logsubdir, bugtool_path),
        'lspci-vv.out',
        logsubdir,
        False)
    # get NIC Names, CPU
    test_conf = xml.dom.minidom.parse(open(testconf_path))

    # XS-version
    for version in test_conf.getElementsByTagName("global_config"):
        if 'xs_version' in version.attributes:
            SERVER_DICT['xs_version'] = version.attributes['xs_version'].value

    # CPU info and HBA pci-id info
    for device in test_conf.getElementsByTagName("device"):
        if 'family' in device.attributes:
            SERVER_DICT['family'] = device.attributes['family'].value
        if 'stepping' in device.attributes:
            SERVER_DICT['stepping'] = device.attributes['stepping'].value
        if 'model' in device.attributes:
            SERVER_DICT['model'] = device.attributes['model'].value
        if 'modelname' in device.attributes:
            SERVER_DICT['modelname'] = device.attributes['modelname'].value
        if 'socket_count' in device.attributes:
            SERVER_DICT['sockets'] = device.attributes['socket_count'].value
        if 'PCI_description' in device.attributes:
            if device.attributes['PCI_description'].value \
                    not in SERVER_DICT['nics']:
                SERVER_DICT['nics'].append(
                    device.attributes['PCI_description'].value
                    )

    # Chassis used info                 i
    lines = open(dmidecode_path).readlines()

    for i in range(len(lines)):
        if re.search('Chassis Information', lines[i]):
            for j in range(len(lines[i:])):
                if re.search("Type", lines[i+j]):
                    mlist = re.findall(r'(\w+):([\w\s\-]+)', lines[i+j])[0]
                    # print "%s" % list
                    SERVER_DICT['chassis'] = mlist[1]
                    break
            break
    # vendor name
    for i in range(len(lines)):
        if re.search("System Information", lines[i]):
            for j in range(len(lines[i:])):
                if re.search("Manufacturer", lines[j+i]):
                    mlist = re.findall(r'([\w\s]+):([\w\s\-\[\]\.]+)',
                                       lines[i+j])[0]
                    SERVER_DICT['system-manufacturer'] = mlist[1]
                    break
            break
    # Pdt name
    for i in range(len(lines)):
        if re.search("System Information", lines[i]):
            for j in range(len(lines[i:])):
                if re.search("Product Name", lines[j+i]):
                    mlist = re.findall(r'([\w\s]+):([\w\s\-\[\]\.^\n]+)',
                                       lines[i+j])[0]
                    SERVER_DICT['product'] = mlist[1]
                    break
            break
    # TODO ONLY IF format of logs are from ackdownload.py,
    # "machine" fetches exists
    # machine = tarfilename.split("-ack")[0]

    # lcpci -vv lines for extracting HBA data(Note: This is a workaround
    # due to existing bug of ACK not catching the right hba pci-ids)
    lines = open(lspcivv_path).readlines()

    # HBAs - Storage Controllers
    string_pattern = [
        'SCSI storage controller',
        'SATA controller',
        'RAID bus controller',
        ]
    index = []
    for i in range(len(lines)):
        for string in string_pattern:
            if re.search(string, lines[i]):
                index.append(i)
    for i in index:
        SERVER_DICT['hbas'].append(re.findall(r'.*: ([\w\s\-\(\)\/\[\]]+)',
                                              lines[i])[0].strip())

    return SERVER_DICT


def display_results(resdict, keys=None):
    """Print out results"""
    # Display rests
    keys = [
        'xs_version',
        'system-manufacturer',
        'product',
        'sockets',
        'chassis',
        'modelname',
        'family',
        'model',
        'stepping',
        'nics',
        'hbas',
    ]
    if keys is None:
        keys = list(resdict.keys())
    for key in keys:
        if type(SERVER_DICT[key]) == 'list':
            print("%50s :" % key, "%-50s" % pprint.pprint(SERVER_DICT[key]))
        else:
            print("%50s : %s" % (key, SERVER_DICT[key]))


def count_test_failures(tarfilename):
    """From a tar file, count failures"""
    testconf_path = extract_file_from_tar(tarfilename, 'test_run.conf',
                                          os.getcwd(), fullpathknown=False)
    test_conf = xml.dom.minidom.parse(open(testconf_path))
    count = 0
    for result in test_conf.getElementsByTagName('result'):
        if result.firstChild.nodeValue != 'pass':
            count = count + 1
    # fail product lists
    return (count, test_conf)


def do_parse(options):
    """do_parse function"""
    tarfilename = options.filename  # downloadACK(runjob, runmachine)
    xenrtmachine = None  # runmachine
    global SERVER_DICT
    SERVER_DICT = result_parser(tarfilename, os.getcwd())
    # display_results(dict, keys)
    display_results(SERVER_DICT)

    (count, test_conf) = count_test_failures(tarfilename)
    testconf_path = extract_file_from_tar(tarfilename, 'test_run.conf',
                                          os.getcwd(), fullpathknown=False)
    fh = open(testconf_path, 'r')
    test_conf_data = fh.read()
    fh.close()

    test_conf = xml.dom.minidom.parseString(test_conf_data)

    if options.post:
        json = xmltojson.ack_xml_to_json(test_conf_data)
        print(json)

    # fail product lists
    if count > 0:
        exception_list = []
        for exception in test_conf.getElementsByTagName('exception'):
            if exception.firstChild.nodeValue not in exception_list:
                exception_list.append(exception.firstChild.nodeValue)
        # maintain MACHINE_DICT only for xenRT machines
        if xenrtmachine:
            FAILED_DICT[xenrtmachine] = exception_list

            if SERVER_DICT['product'] not in MACHINE_DICT['pass']:
                if SERVER_DICT['product'] not in MACHINE_DICT['fail']:
                    MACHINE_DICT['fail'].append(SERVER_DICT['product'])
            print("*******%s tests FAILED for %s *********" % (
                count,
                xenrtmachine,
                ))
        else:
            FAILED_DICT[SERVER_DICT['product']] = exception_list
    else:
        # added check here
        if xenrtmachine:  # and SERVER_DICT['product'] not in passed_list:
            # remove duplicacy
            if SERVER_DICT['product'] not in MACHINE_DICT['pass']:
                MACHINE_DICT['pass'].append(SERVER_DICT['product'])
    if xenrtmachine:
        print("#"*30)
        print("NICS LISTING HERE")
        display_results(NICS_DICT)
        print("NICS LISTING OVER")
        print("#"*30)
        print("HBAs HERE")
        display_results(HBAS_DICT)
        print("HBAs listing over")
        print("#"*30)
        print("MY PASSED PRODUCTS")
        pprint.pprint(MACHINE_DICT['pass'])
        print("#"*30)
        print("MY FAIL PRODUCTS")
        pprint.pprint(MACHINE_DICT['fail'])
        print("#"*30, "FAILED_DICT below")
    display_results(FAILED_DICT)


def get_json_from_test_run(tar_filename):
    testconf_path = extract_file_from_tar(tar_filename, 'test_run.conf',
                                          os.getcwd(), fullpathknown=False)
    fh = open(testconf_path, 'r')
    test_conf_data = fh.read()
    fh.close()

    test_conf = xml.dom.minidom.parseString(test_conf_data)
    json = xmltojson.ack_xml_to_json(test_conf_data)
    return json


def post_json_to_mongodb(json):
    client = MongoClient('mongodb://localhost:27018/')
    db = client.certification
    sub = db.submissions
    sub_id = sub.insert(json)
    return sub_id


def validate_test_run(json):
    xs_version = json['global_config']['xs_version'].split('.')
    xs_version = tuple([int(i) for i in xs_version])

    for dev in json['devices']:
        driver = ""
        print("")
        if dev['tag'] == 'NA':
            print(dev['PCI_description'])
            print("Driver: %s %s" % (dev['Driver'], dev['Driver_version']))
            print("Firmware: %s" % dev['Firmware_version'])
            driver = dev['Driver']
        if dev['tag'] == 'CPU':
            print(dev['modelname'])
        if dev['tag'] == 'LS':
            if 'product_version' in dev:
                print(dev['PCI_description'])
            elif "driver" in dev:
                print(dev['driver'])
                driver = dev['driver']
        if dev['tag'] == 'OP':
            if 'product_version' in dev:
                print(dev['product_version'])
            else:
                print(dev['version'])

        # CP-30556: Deprecate Marvell (Qlogic) legacy drivers for CH 8.0
        if xs_version >= (8, 0, 0) and driver in DRIVER_BLACK_LIST:
            print("Error: The driver is already deprecated, " \
                    "do not list the hardware on HCL!")

        passed = []
        failed = []
        ignored = []

        for test in dev['tests']:
            if test['result'] == "pass":
                passed.append(test)
            elif test['result'] == "fail":
                failed.append(test)
            else:
                ignored.append(test)

        if passed:
            print("Passed:")
        for t in passed:
            print(t['test_name'])
        print("")

        if failed:
            print("Failed:")
        for t in failed:
            print(t['test_name'])

        if ignored:
            print("Ignored (skipped/other):")
        for t in ignored:
            print(t['test_name'])


def parse_submission(args):
    # Extract the submission
    tmpdir = tempfile.mkdtemp()
    bugtool = inspector.find_in_tarball(args.filename, 'tar.bz2')
    tar = tarfile.open(args.filename)
    t = tar.extract(bugtool, tmpdir)

    tarball_path = "%s/%s" % (tmpdir, bugtool)
    host = inspector.HostFromTarball(tarball_path)

    print(inspector.system_info(host, ['bios', 'cpu', 'nic', 'storage']))
    shutil.rmtree(tmpdir)

    json = get_json_from_test_run(args.filename)

    # Check for failures
    validate_test_run(json)

    if args.post:
        print(post_json_to_mongodb(json))


def main():
    """Entry point"""
    parser = ArgumentParser()
    parser.add_argument("-f", "--file", dest="filename", required=True,
                        help="ACK tar file")
    parser.add_argument("-p", "--post", dest="post", action="store_true")
    args = parser.parse_args()
    parse_submission(args)
    compare_submission_rpms_with_repos(args.filename)
