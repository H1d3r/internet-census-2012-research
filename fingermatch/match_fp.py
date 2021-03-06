#!/usr/bin/python

"""
Parses an nmap-os-db file, then reads an Nmap fingerprint from the standard
input and prints the matches.

Currently it's quite slow and not completely compatible with Nmap. It already
proved useful in finding errors in nmap-os-db database.

TODO: make it less strict about whitespaces
"""

import sys
import os
import copy
import datetime
import re
import argparse
from io import StringIO
from fputils import print_stderr

# A dictionary of tables with known tests. Any test not listed here is
# considered an error.
known_tests = {
  'SEQ': ['SP', 'GCD', 'ISR', 'TI', 'CI', 'II', 'SS', 'TS'],
  'OPS': ['O1', 'O2', 'O3', 'O4', 'O5', 'O6'],
  'WIN': ['W1', 'W2', 'W3', 'W4', 'W5', 'W6'],
  'ECN': ['R', 'DF', 'T', 'TG', 'W', 'O', 'CC', 'Q'],
  'T1': ['R', 'DF', 'T', 'TG', 'S', 'A', 'F', 'RD', 'Q'],
  'T2': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'T3': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'T4': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'T5': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'T6': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'T7': ['R', 'DF', 'T', 'TG', 'W', 'S', 'A', 'F', 'O', 'RD', 'Q'],
  'U1': ['R', 'DF', 'T', 'TG', 'IPL', 'UN',
         'RIPL', 'RID', 'RIPCK', 'RUCK', 'RUD'],
  'IE': ['R', 'DFI', 'T', 'TG', 'CD'],
}


def explain_with_dict(d, default=None):
  """Returns a function that explains given values using a specified
  dictionary.

  Args:
    d (dict): the dictionary used for explaining the values
    default: the default value to be returned if the key is not found

  Example:
  >>> f = explain_with_dict({'A': 'Good', 'B': 'Bad'})
  >>> f('A')
  'Good'
  >>> f('C')
  Traceback (most recent call last):
    File "<stdin>", line 1, in <module>
    File "<stdin>", line 26, in inner_function
  KeyError: 'C'
  >>> f = explain_with_dict({'A': 'Good', 'B': 'Bad'}, default='No idea')
  >>> f('C')
  'No idea'

  Returns function
  """
  def inner_function(k):
    """Inner function for explain_with_dict. Returns a value under the given
    key in the outer dictionary's argument.

    Args:
      k: the key for the outer function's dictionary
    """
    if default is not None and k not in d:
      return default
    else:
      return '%s' % d[k]
  return inner_function


def hextimestamp_to_date(hextimestamp):
  """Converts a hexadecimal timestamp to a human-readable date.

  Args:
    hextimestamp (str): the timestamp expressed as a hexadecimal number

  Example:
  >>> hextimestamp_to_date("5045AD58")
  '2012-09-04 09:27:20'

  Returns str
  """
  ret = datetime.datetime.fromtimestamp(int(hextimestamp, 16))
  return str(ret)


def explain_option(option):
  """Explain a single Nmap TCP option

  Args:
    option (str): the TCP option atom to be explained

  Returns str
  """
  c = option[0]
  ret = '<'

  if c == 'L':
    ret += 'End of Options'
  elif c == 'N':
    ret += 'No operation'
  elif c == 'M':
    ret += 'Maximum Segment Size'
  elif c == 'W':
    ret += 'Window Scale'
  elif c == 'T':
    ret += 'Timestamp'
  elif c == 'S':
    ret += 'Selective ACK permitted'

  if len(option) > 1:
    ret += ' [%s]' % option[1:]

  return ret + '>'


def explain_options(options):
  """Explains Nmap TCP options syntax.

  Args:
    options (str): the TCP options string to be explained

  Returns str
  """
  options_split = re.findall('([LNMWTS][0-9A-F]*)', options)
  options_list = map(lambda x: explain_option(x), options_split)
  if options_list == []:
    return 'no options'
  return ', '.join(options_list)


def explain_flags(flags):
  """Explains Nmap TCP flags syntax.

  Args:
    flags (str): the TCP flags to be explained

  Returns str
  """
  ret = []
  if 'E' in flags:
    ret += ['ECN Echo']
  if 'U' in flags:
    ret += ['Urgent Data']
  if 'A' in flags:
    ret += ['Acknowledgement']
  if 'P' in flags:
    ret += ['Push']
  if 'R' in flags:
    ret += ['Synchronize']
  if 'F' in flags:
    ret += ['Final']
  return ', '.join(ret)

# The following explanations can be found either in Gordon "Fyodor" Lyon's book
# titled "Nmap Network Scanning" (I totally recommend it) or in the chapter 8
# here:
#
# http://nmap.org/book/toc.html

seq__ti_ci_ii_expl = explain_with_dict({
  'Z': 'all zero',
  'RD': 'random - at least one increase by 20 000',
  'RI': 'random positive increments - difference > 1000 '
        'and difference mod 256 not even',
  'BI': 'broken - divisible by 256, no greater than 5120',
  'I': 'incremental - all of the differences less than ten',
}, default='identical')

quirks_explanation = ['TCP miscellaneous quirks', explain_with_dict({
  '': 'no quirks present',
  'R': 'reserved field in the TCP header is nonzero',
  'RU': 'reserved field in the TCP header is nonzero AND '
        'nonzero urgent pointer field value when URG flag is not set',
  'U': 'nonzero urgent pointer field value when URG flag is not set',
})]

first_four = {
  'R': ['Responsiveness', None],
  'DF': ['IP don\'t fragment bit', None],
  'T': ['IP initial time-to-live', None],
  'TG': ['IP initial time-to-live guess', None],
}

t1_explanation = copy.copy(first_four)
t1_explanation.update({
  'S': ['TCP sequence number', explain_with_dict({
      'Z': 'sequence number is zero',
      'A': 'sequence number = acknowledgement number in the probe',
      'A+': 'sequence number = acknowledgement number in the probe plus one',
      'O': 'other (not zero, not acknowledgement number plus zero/one)',
    })],
  'A': ['TCP acknowledge number', explain_with_dict({
      'Z': 'acknowledgement number is zero',
      'S': 'acknowledgement number = sequence number in the probe',
      'S+': 'acknowledgement number = sequence number in the probe plus one',
      'O': 'other (not zero, not sequence number plus zero/one)',
    })],
  'F': ['TCP flags', explain_flags],
  'RD': ['TCP RST data checksum', explain_with_dict({
    '0': 'no data'
    }, default='present')],
  'Q': quirks_explanation,
})

t2_t7_explanation = copy.copy(t1_explanation)
t2_t7_explanation.update({
  'W': ['TCP initial window size for ECN packet', None],
  'O': ['TCP options for ECN packet', explain_options],
})

u1_explanation = copy.copy(first_four)
u1_explanation.update({
  'IPL': ['IP total length', None],
  'UN': ['Unused port unreachable field nonzero', None],
  'RIPL': ['Returned probe IP total length value', None],
  'RID': ['Returned probe IP ID value', None],
  'RIPCK': ['Integrity of returned probe IP checksum value', None],
  'RUCK': ['Integrity of returned probe UDP length and checksum',
           None],
  'RUD': ['Integrity of returned UDP data', None],
})

test_explanations = {
  'SCAN': ['General information about the tests', {
    'V': ['Nmap version used to perform the scan', None],
    'D': ['Date of scan (M/D)', None],
    'E': ['OS detection engine ID', None],
    'OT': ['Open TCP port number', None],
    'CT': ['Closed TCP port number', None],
    'CU': ['Closed UCP port number', None],
    'PV': ['Private IP space', None],
    'DS': ['Network distance in hops', None],
    'DC': ['Distance calculation method', explain_with_dict({
        'L': 'localhost',
        'D': 'direct',
        'I': 'ICMP',
        'T': 'traceroute',
      })],
    'G': ['Fingerprint suitable for submission', None],
    'M': ['Mac address without leading zeros', None],
    'TM': ['Scan time in hexadecimal epoch', hextimestamp_to_date],
    'P': ['Nmap platform', None],
  }],
  'SEQ': ['Packet sequence analysis', {
    'SP': ['TCP ISN sequence predictability index', None],
    'GCD': ['TCP ISN greatest common divisor', None],
    'ISR': ['TCP ISN counter rate', None],
    'TI': ['TCP IP ID sequence generation algorithm', seq__ti_ci_ii_expl],
    'CI': ['TCP IP ID closed port sequence numbers', seq__ti_ci_ii_expl],
    'II': ['ICMP IP ID sequence generation algorithm', seq__ti_ci_ii_expl],
    'SS': ['Shared IP ID sequence Boolean', explain_with_dict({
        'S': 'the sequence is shared',
        'O': 'the sequence is not shared',
      })],
    'TS': ['TCP timestamp option algorithm', explain_with_dict({
        'U': 'unsupported - any of the responses has no timestamp option',
        '0': 'zero - any of the timestamp values are zero',
        '1': 'average increments per second falls within 0-5.66',
        '7': 'average increments per second falls within 70-150',
        '8': 'average increments per second falls within 150-350',
        'A': '1,000 Hz',
      }, default='binary logarithm of the average increments per second, '
                 'rounded to the nearest integer')],
  }],
  'OPS': ['TCP options', {
    'O1': ['TCP options for packet 1', explain_options],
    'O2': ['TCP options for packet 2', explain_options],
    'O3': ['TCP options for packet 3', explain_options],
    'O4': ['TCP options for packet 4', explain_options],
    'O5': ['TCP options for packet 5', explain_options],
    'O6': ['TCP options for packet 6', explain_options],
  }],
  'WIN': ['TCP initial window size', {
    'W1': ['TCP initial window size for packet 1', None],
    'W2': ['TCP initial window size for packet 2', None],
    'W3': ['TCP initial window size for packet 3', None],
    'W4': ['TCP initial window size for packet 4', None],
    'W5': ['TCP initial window size for packet 5', None],
    'W6': ['TCP initial window size for packet 6', None],
  }],
  'ECN': ['TCP explicit congestion notification', {
    'R': ['Responsiveness', None],
    'DF': ['IP don\'t fragment bit', None],
    'T': ['IP initial time-to-live', None],
    'TG': ['IP initial time-to-live guess', None],
    'W': ['TCP initial window size for ECN packet', None],
    'O': ['TCP options for ECN packet', explain_options],
    'CC': ['Explicit congestion control', None],
    'Q': quirks_explanation,
  }],
  'T1': ['TCP probe no. 1 - window scale (10), NOP, MSS (1460), '
         'timestamp (TSval: 0xFFFFFFFF; TSecr: 0), SACK permitted. '
         'The window field is 1.', t1_explanation],
  'T2': ['TCP probe no. 2 - no flags set, IP DF set, '
         'window=128 to an open port', t2_t7_explanation],
  'T3': ['TCP probe no. 3 - SYN, FIN, URG, PSH, '
         'window=256 to open port, IP DF not set', t2_t7_explanation],
  'T4': ['TCP probe no. 4 - TCP ACK with IP DF and '
         'window=1024 to an open port', t2_t7_explanation],
  'T5': ['TCP probe no. 5 - TCP SYN without IP DF and '
         'window=31337 to a closed port', t2_t7_explanation],
  'T6': ['TCP probe no. 6 - TCP ACK with IP DF and '
         'window=32768 to a closed port', t2_t7_explanation],
  'T7': ['TCP probe no. 7 - FIN, PSH and URG set, window=63535 '
         'to a closed port, IP DF not set', t2_t7_explanation],
  'U1': ['UDP probe no. 1 - character \'C\' repeated '
         '300 times, IP ID set to 0x1024', u1_explanation],
  'IE': ['ICMP echo', {
    'R': ['Responsiveness', None],
    'DFI': ['Don\'t fragment (ICMP)', explain_with_dict({
        'N': 'neither of the ping responses have the DF bit set',
        'S': 'both responses echo the DF value of the probe',
        'Y': 'both of the response DF bits are set',
        'O': 'other - both responses have the DF bit toggled',
      })],
    'T': ['IP initial time-to-live', None],
    'TG': ['IP initial time-to-live guess', None],
    'CD': ['ICMP response code', explain_with_dict({
        'Z': 'both code values are zero',
        'S': 'both code values are the same as in the corresponding probe',
        'O': 'other: the ICMP response codes vary',
      }, default='both packets use the same non-zero number')],
  }],
}


class Fingerprint:
  """A class that holds data about a single fingerprint."""

  def __init__(self):
    self.name = ""  # The name from the "Fingerprint " line in nmap-os-db
    self.classes = ""  # The "Class " field
    self.cpe = ""  # "The CPE field"
    self.line = 0  # Line number in nmap-os-db
    self.score = 0  # Total number of points gathered in a matching attempt

    # Probes dictionary. Its keys are group tests (WIN, U1, etc), the values
    # are either None (if R=N) or a nested dictionary, in which the keys are
    # the test names and values are either lambdas (in a pattern fingerprint,
    # from nmap-os-db) or strings (in an Nmap-generated fingerprint).
    #
    # Example 1 - a part of 'Juniper MAG2600 SSL VPN gateway' fingerprint:
    #
    # self.probes = {
    # 'T1': {
    #     # (that could be generated by parse_test)
    #     'T': lambda x: is_hex(x) and int(x, 16) >= 59 and int(x, 16) <= 69,
    #   },
    # }
    #
    # Example 2 - a part of an Nmap-generated fingerprint:
    # self.probes = {
    # 'T1' : {
    #     'T': '3E',
    #   },
    # }
    self.probes = {}


class ReprWrapper:
  """A class that wraps around an object, allowing the user to decide how will
  it be displayed by __repr__. Indended for a readable get_matchpoints.

  Usage:

  >>> l = ReprWrapper(lambda: 3, 'spam')
  >>> l
  'spam'
  >>> l()
  3
  """

  def __init__(self, expr, str_show):
    self.l = expr
    self.expr = expr
    self.str_show = repr(str_show)

  def __getattr__(self, arg):
    """This is called whenever a method unknown to ReprWrapper is called. This
    includes __call__, so an attempt to call ReprWrapper object will result in
    actually calling the lambda."""
    return getattr(self.l, arg)

  def __str__(self):
    return self.str_show

  def __repr__(self):
    return self.str_show


def get_matchpoints(f):
  """Read matchpoints from a file. Strictly validate the input. Returns the
  sum of the points that a fingerprint can score, a dictionary where the keys
  are test group names and values are nested dictionaries with test names as
  keys and the points to be gained for passing the tests as the values.

  Args:
    f (file): the file to read the matchpoints from

  Returns int, dict, int
  """
  matchpoints = {}
  max_points = 0
  lines_read = 0
  while True:
    line = f.readline()
    # crash on EOF
    assert(line != '')
    lines_read += 1
    if line == '\n':
      break
    group_name, tests = line.split('(')
    # make sure it's not a redefinition of a test group and the group is known
    assert(group_name not in matchpoints)
    assert(group_name in known_tests)
    matchpoints[group_name] = {}
    for test in tests.rstrip(')\n').split('%'):
      test_name, test_points = test.split('=')
      # make sure it's not a redefinition of a test and the test is known
      assert(test not in matchpoints[group_name])
      assert(test_name in known_tests[group_name])
      matchpoints[group_name][test_name] = int(test_points)
      max_points += int(test_points)
  return max_points, matchpoints, lines_read


def tests_repr(dict_, group_name, _known_tests, sep=' '):
  """A __repr__ for dictionaries that displays key-value pairs in a sorted
  order.

  Args:
    dict_ (dict): the dictionary to be described
    group_name (str): the name of the test group, used to adjust sorting order
    _known_tests (dict): a dictionary with the known tests
    sep (str): the key-value pair separator

  Returns str
  """
  ret = []
  unknown_tests = [key for key in dict_ if key not in _known_tests[group_name]]
  if unknown_tests != []:
    print_stderr("WARNING: tests_repr: unknown_tests=%s" % repr(unknown_tests))
  for k in _known_tests[group_name] + unknown_tests:
    if k not in dict_:
      continue
    ret += ["%s: %s" % (repr(k), repr(dict_[k]))]
  return '{' + (',' + sep).join(ret) + '}'


def print_probes(probe_dict, _known_tests, sep=' '):
  """Pretty-prints a given probe. Adds newlines, sorts the dictionaries and
  aligns the key lengths.

  Args:
    probe_dict (dict): a dictionary with the probes
    _known_tests (dict): a dictionary with the known tests
    sep (str): the separator that will be passed to tests_repr

  Returns None
  """
  print('{')
  unknown_groups = [key for key in probe_dict if key not in _known_tests]
  if unknown_groups != []:
    print_stderr("WARNING: print_probes: unknown_groups=%s" %
                 repr(unknown_groups))
  for k in ['SCAN', 'SEQ', 'OPS', 'WIN', 'ECN', 'T1', 'T2',
            'T3', 'T4', 'T5', 'T6', 'T7', 'U1', 'IE'] + unknown_groups:
    if k not in probe_dict:
      continue
    if isinstance(probe_dict[k], list):
      desc = sorted(probe_dict[k])
    elif isinstance(probe_dict[k], dict):
      desc = tests_repr(probe_dict[k], k, _known_tests, sep)
    else:
      desc = repr(probe_dict[k])
    line = '  %5s: %s,' % (repr(k), desc)
    print(line)
  print('}')


def is_hex(x):
  """Returns true if the value can be considered a hexadecimal number, false
  otherwise.

  Returns bool
  """
  try:
    int(x, 16)
    return True
  except ValueError:
    return False


def parse_test_expr(test):
  """Parses a test expression. Returns a list with the test names, the value
  expression and ReprWrapper that matches the expression.

  Args:
    test (str): the test expression. Example: W1|W2=0|5B40

  Returns list, str, ReprWrapper
  """
  # find all the test names
  test_names = []
  i = 0
  start = i
  while test[i] != '=':
    while test[i].isalnum():
      i += 1
    test_name = test[start:i]
    test_names += [test_name]
    start = i
    assert(test[i] in ['=', '|'])
    if test[i] == '|':
      i += 1
      start += 1

  # build a ReprWrapper based on the test expression
  test_exp = test[i + 1:]
  exps = test_exp.split('|')
  lambda_code = 'lambda x: '
  lambda_exps = []
  for exp in exps:
    if exp == '':
      exp = "''"
    if exp[0] in ['>', '<']:
      lambda_exps += ['x %s "%s"' % (exp[0], exp[1:])]
    elif exp.find('-') != -1:
      lower_bound_hex, upper_bound_hex = exp.split('-')
      lower_bound = int(lower_bound_hex, 16)
      upper_bound = int(upper_bound_hex, 16)
      lambda_exps += ['is_hex(x) and int(x, 16) >= %d and int(x, 16) <= %d' %
                      (lower_bound, upper_bound)]
    else:
      lambda_exps += ['x == "%s"' % exp]
  lambda_code += ' or '.join(lambda_exps)
  test_lambda = ReprWrapper(eval(lambda_code), test_exp)
  return test_names, test_exp, test_lambda


def explain_fp(probe_dict, _known_tests):
  """Print an explantion of a given fingerprint to the standard output.

  Args:
    probe_dict (dict): a dictionary with the tests result
    _known_tests (dict): a dictionary with the known tests

  Returns None
  """
  unknown_groups = [key for key in probe_dict if key not in _known_tests]
  if unknown_groups != []:
    print_stderr("WARNING: print_probes: unknown_groups=%s" %
                 repr(unknown_groups))
  for k in ['SCAN', 'SEQ', 'OPS', 'WIN', 'ECN', 'T1', 'T2',
            'T3', 'T4', 'T5', 'T6', 'T7', 'U1', 'IE'] + unknown_groups:
    if k not in test_explanations:
      print_stderr("WARNING: unknown test group: %s (values: %s)" % (k,
                   repr(probe_dict[k])))
      continue
    print("%s (%s):" % (test_explanations[k][0], k))
    if k not in probe_dict:
      print("\t(test group not found)")
      continue
    if probe_dict[k] is None:
      print("\t(no response to the probe)")
      continue
    for test in _known_tests[k]:
      test_explanation = test_explanations[k][1][test]
      explanation_f = test_explanation[1]
      if test in probe_dict[k]:
        if explanation_f is not None:
          explanation = explanation_f(probe_dict[k][test])
          value_explanation = "%s (%s)" % (probe_dict[k][test], explanation)
        else:
          value_explanation = "%s" % probe_dict[k][test]
      else:
        value_explanation = "(no information)"
      print("\t%s (%s): %s" % (test_explanation[0], test, value_explanation))


def load_fingerprints():
  fingerprints = []
  fp_db_file = 'nmap-os-db2'
  f = open(fp_db_file)
  got_fp = False
  fp = Fingerprint()
  lineno = 0
  while True:
    line = f.readline()
    lineno += 1
    if line == '\n' or line == '':
    # we hit a newline or an EOF, consider than an end of a fingerprint entry
      if got_fp:
        # make sure we collected all the known tests
        # and register the fingerprint
        assert(all(test in fp.probes for test in known_tests))
        fingerprints += [fp]
        fp = Fingerprint()
      if line == '':
        break
    elif line[0] == '#':
      # ignore the comments
      continue
    elif line.startswith("MatchPoints"):
      max_points, matchpoints, lines_read = get_matchpoints(f)
      lineno += lines_read
      p = {}
    elif line.startswith("Fingerprint "):
      fp.name = line[len("Fingerprint "):].rstrip('\r\n')
      fp.line = lineno
    elif line.startswith("Class "):
      fp.clases = line[len("Class "):]
    elif line.startswith("CPE "):
      fp.cpe = line[len("CPE "):]
    # see if the line starts with a definition of any known test group
    elif any(line.startswith(k + "(") for k in known_tests):
      group_name, tests = line.split('(')
      # make sure it's not a redefinition of a test group
      # and the group is known
      assert(group_name not in fp.probes)
      assert(group_name in known_tests)
      fp.probes[group_name] = {}
      for test in tests.rstrip(')\n').split('%'):
        if test == '':  # treat lines like 'OPS()' as 'OPS(R=N)'
          fp.probes[group_name] = None
          continue
        test_names, test_exp, test_lambda = parse_test_expr(test)
        for test_name in test_names:
          # make sure it's not a redefinition of a test. Commented out because
          # nmap-os-db currently contains redefinitions.
          if test_name in fp.probes[group_name]:
            print_stderr("WARNING: %s:%d: duplicate %s" % (fp_db_file, lineno,
                                                           test_name))
          if test_name == 'R' and test_exp == "N":
            fp.probes[group_name] = None
            continue
          # make sure it's a known test. there are four exceptions because of
          # errors in nmap-os-db.
          if test_name in ['W0', 'W7', 'W8', 'W9']:
            pass
          else:
            assert(test_name in known_tests[group_name])
          fp.probes[group_name][test_name] = test_lambda
      got_fp = True
    else:
      sys.exit("ERROR: Strange line in %s: '%s'" % (fp_db_file, repr(line)))

  print_stderr("Loaded %d fingerprints." % len(fingerprints))
  return fingerprints, matchpoints, max_points

def handle_test(fp, group_name, test_name, value):
  if group_name == 'SCAN':
    return
  if test_name == 'R' and value == "N":
    fp.probes[group_name] = None
  elif test_name in ['W0', 'W7', 'W8', 'W9']:
    pass
  else:
    assert(test_name in fp_known_tests[group_name])
    fp.probes[group_name][test_name] = value
  for fingerprint in fingerprints:
    points = matchpoints[group_name][test_name]
    if fingerprint.probes[group_name] is None:
      if test_name == 'R' and value == 'N':
        fingerprint.score += sum(matchpoints[group_name].values())
    elif not test_name in fingerprint.probes[group_name]:
      return
    elif fingerprint.probes[group_name][test_name](value):
      fingerprint.score += points

def read_until(chars, f, error_if=None, skip_whitespace=True):
    ret = StringIO()
    while True:
        c = f.read(1)
        if c == '':
            raise ValueError("Caught an end of file while "
                             "reading until '%s'" % chars)
        if c in chars:
            break
        if c.isspace() and skip_whitespace:
            continue
        if error_if and error_if(c):
            raise RuntimeError("Unexpected %s at byte %d" % (repr(c),
                                                             f.tell()))
        ret.write(unicode(c))
    return str(ret.getvalue()), c

def parse_fp(f, fp):
    while True:
        try:
            group_name, _ = read_until(['('], f, lambda c: not c.isalnum())
            if group_name in fp.probes:
                raise RuntimeError("Redefined group: %s" % group_name)
            if group_name not in fp_known_tests:
                raise RuntimeError("Unknown group: %s" % group_name)
            fp.probes[group_name] = {}
        except ValueError:
            break
        while True:
            test_name, _ = read_until(['='], f, lambda c: not c.isalnum())
            value, sep = read_until(['%', ')'], f, lambda c: c in ['('])
            handle_test(fp, group_name, test_name, value)
            if sep == ')':
                break

if __name__ == "__main__":

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--match', action='store_true')
  parser.add_argument('--explain', action='store_true')
  args = parser.parse_args()
  if not args.match and not args.explain:
    sys.exit("ERROR: %s: you need either --match or --explain to continue."
             % sys.argv[0])

  if args.match:
    fingerprints, matchpoints, max_points = load_fingerprints()
  else:
    fingerprints = []

  if os.isatty(sys.stdin.fileno()):
    print_stderr("Please enter the fingerprint in Nmap format:")

  fp_known_tests = copy.copy(known_tests)
  fp_known_tests['SCAN'] = ['V', 'E', 'D', 'OT', 'CT', 'CU',
                            'PV', 'DS', 'DC', 'G', 'TM', 'P', 'M']
  fp = Fingerprint()
  parse_fp(sys.stdin, fp)

  if args.match:
    fps = list(reversed(sorted(fingerprints, key=lambda x: x.score)))
    print("Best matches:")
    for i in range(10):
      score = float(fps[i].score) / max_points * 100
      print("[%.2f%%] %s" % (score, fps[i].name))

  if args.explain:
    explain_fp(fp.probes, fp_known_tests)
