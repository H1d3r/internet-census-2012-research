#!/usr/bin/python

"""
Parses an nmap-os-db file, then reads an Nmap fingerprint from the standard
input and prints the matches.

Currently it's quite slow and not completely compatible with Nmap. It already
proved useful in finding errors in nmap-os-db database.
"""

import sys
import os
import copy
from fputils import print_stderr

# A dictionary of tables with known tests. Any test not listed here is
# considered an error.
known_tests = {
  'ECN': ['CC', 'DF', 'O', 'Q', 'R', 'T', 'TG', 'W'],
   'IE': ['CD', 'DFI', 'R', 'T', 'TG'],
  'OPS': ['O1', 'O2', 'O3', 'O4', 'O5', 'O6'],
  'SEQ': ['CI', 'GCD', 'II', 'ISR', 'SP', 'SS', 'TI', 'TS'],
   'T1': ['A', 'DF', 'F', 'Q', 'R', 'RD', 'S', 'T', 'TG'],
   'T2': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'T3': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'T4': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'T5': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'T6': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'T7': ['A', 'DF', 'F', 'O', 'Q', 'R', 'RD', 'S', 'T', 'TG', 'W'],
   'U1': ['DF', 'IPL', 'R', 'RID', 'RIPCK',
          'RIPL', 'RUCK', 'RUD', 'T', 'TG', 'UN'],
  'WIN': ['W1', 'W2', 'W3', 'W4', 'W5', 'W6'],
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


class PrettyLambda:
  """A class that wraps around a lambda object, allowing the user to decide
  how will it be displayed by __repr__. Indended for a readable
  get_matchpoints.

  Usage:

  >>> l = PrettyLambda('lambda: 3', 'spam')
  >>> l
  'spam'
  >>> l()
  3
  """

  def __init__(self, expr, str_show):
    self.l = eval(expr)
    self.expr = expr
    self.str_show = repr(str_show)

  def __getattr__(self, arg):
    """This is called whenever a method unknown to PrettyLambda is called. This
    includes __call__, so an attempt to call PrettyLambda object will result
    in actually calling the lambda."""
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


def sorted_dict_repr(dict_, sep=' '):
  """A __repr__ for dictionaries that displays key-value pairs in a sorted
  order.

  Example:

  >>> print(sorted_dict_repr({5: 6, 7:9, 2: 4}, sep='\n'))
  {2: 4,
  5: 6,
  7: 9}

  Args:
    dict_ (dict): the dictionary to be described
    sep (str): the key-value pair separator
  Returns str
  """
  ret = []
  for k in sorted(dict_):
    ret += ["%s: %s" % (repr(k), repr(dict_[k]))]
  return '{' + (',' + sep).join(ret) + '}'


def print_probes(probe_dict, sep=' '):
  """Pretty-prints a given probe. Adds newlines, sorts the dictionaries and
  aligns the key lengths.

  Args:
    probe_dict (dict): a dictionary with the probes
    sep (str): the separator that will be passed to sorted_dict_repr

  Returns None
  """
  print('{')
  for k in sorted(probe_dict):
    if isinstance(probe_dict[k], list):
      desc = sorted(probe_dict[k])
    elif isinstance(probe_dict[k], dict):
      desc = sorted_dict_repr(probe_dict[k], sep)
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


def parse_test(test):
  """Parses a test expression. Returns a list with the test names, the value
  expression and PrettyLambda that matches the expression.

  Args:
    test (str): the test expression. Example: W1|W2=0|5B40

  Returns list, str, PrettyLambda
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

  # build a PrettyLambda based on the test expression
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
  test_lambda = PrettyLambda(lambda_code, test_exp)
  return test_names, test_exp, test_lambda

fp_db_file = 'nmap-os-db2'
fingerprints = []
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
      # make sure we collected all the known tests and register the fingerprint
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
    fp.name = line[len("Fingerprint "):]
    fp.line = lineno
  elif line.startswith("Class "):
    fp.clases = line[len("Class "):]
  elif line.startswith("CPE "):
    fp.cpe = line[len("CPE "):]
  # see if the line starts with a definition of any known test group
  elif any(line.startswith(k + "(") for k in known_tests):
    group_name, tests = line.split('(')
    # make sure it's not a redefinition of a test group and the group is known
    assert(group_name not in fp.probes)
    assert(group_name in known_tests)
    fp.probes[group_name] = {}
    for test in tests.rstrip(')\n').split('%'):
      if test == '':  # treat lines like 'OPS()' as 'OPS(R=N)'
        fp.probes[group_name] = None
        continue
      test_names, test_exp, test_lambda = parse_test(test)
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

if os.isatty(sys.stdin.fileno()):
  print_stderr("Please enter the fingerprint in Nmap format:")

fp_known_tests = copy.copy(known_tests)
fp_known_tests['SCAN'] = ['V', 'E','D','OT','CT','CU',
                          'PV','DS','DC','G','TM','P']
fp = Fingerprint()
for line in sys.stdin.xreadlines():
  if any(line.startswith(k + "(") for k in fp_known_tests):
    group_name, tests = line.split('(')
    assert(group_name not in fp.probes)
    assert(group_name in fp_known_tests)
    fp.probes[group_name] = {}
    for test in tests.rstrip(')\n').split('%'):
      if test == '':
        fp.probes[group_name] = None
        continue
      test_name, value = test.split('=')
      if test_name == 'R' and value == "N":
        fp.probes[group_name] = None
      elif test_name in ['W0', 'W7', 'W8', 'W9']:
        pass
      else:
        assert(test_name in fp_known_tests[group_name])
        fp.probes[group_name][test_name] = value
      if group_name == 'SCAN':
        continue
      for fingerprint in fingerprints:
        points = matchpoints[group_name][test_name]
        if fingerprint.probes[group_name] is None:
          if test_name == 'R' and value == 'N':
            fingerprint.score += sum(matchpoints[group_name].values())
        elif not test_name in fingerprint.probes[group_name]:
          continue
        elif fingerprint.probes[group_name][test_name](value):
          fingerprint.score += points
  else:
    print_stderr("WARNING: weird line: %s" % line)

fps = list(reversed(sorted(fingerprints, key=lambda x: x.score)))

print("Best matches:")
for i in range(10):
  score = float(fps[i].score) / max_points * 100
  print("[%.2f%%] %s" % (score, fps[i].name))
