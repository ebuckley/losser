from io import BytesIO
import collections
import itertools
import json
import pprint
import re

import tabulate
import unicodecsv


class UniqueError(Exception):
    pass


class InvalidColumnsFileError(Exception):

    """Exception raised when reading a columns.json file fails."""

    pass


def _read_columns_file(f):
    """Return the list of column queries read from the given JSON file.

    :param f: path to the file to read
    :type f: string

    :rtype: list of dicts

    """
    try:
        columns = json.loads(open(f, 'r').read(),
                             object_pairs_hook=collections.OrderedDict)
    except Exception as err:
        raise InvalidColumnsFileError(
            "There was an error while reading {0}: {1}".format(f, err))

    # Options are not supported yet:
    if '__options' in columns:
        del columns['__options']

    return columns


def _write_csv(f, table_):
    """Write the given table (list of dicts) to the given file as CSV.

    Writes UTF8-encoded, CSV-formatted text.

    ``f`` could be an opened file, sys.stdout, or a BytesIo.

    """
    fieldnames = list(table_[0].keys())
    set_fieldname = set(table_[0].keys())
    # go through all the fields and find all the field names
    for row in table_:
        set_fieldname.update(set(row.keys()))

    # append the additonal fields sorted onto the end
    additional_fields = sorted(set_fieldname - set(table_[0].keys()))
    fieldnames += additional_fields

    writer = unicodecsv.DictWriter(f, fieldnames, encoding='utf-8')
    writer.writeheader()

    # Change lists into comma-separated strings.
    for dict_ in table_:
        for key, value in list(dict_.items()):
            if type(value) in (list, tuple):
                dict_[key] = ', '.join([v for v in value])

    writer.writerows(table_)


def _table_to_csv(table_):
    """Return the given table converted to a CSV string.

    :param table: the table to convert
    :type table: list of OrderedDicts each with the same keys in the same
        order

    :rtype: string, CSV-formatted string

    """
    f = BytesIO()
    try:
        _write_csv(f, table_)
        return f.getvalue().decode('utf-8')
    finally:
        f.close()


def table(dicts, columns, csv=False, pretty=False):
    """Query a list of dicts with a list of queries and return a table.

    A "table" is a list of OrderedDicts each having the same keys in the same
    order.

    :param dicts: the list of input dicts
    :type dicts: list of dicts

    :param columns: the list of column query dicts, or the path to a JSON file
        containing the list of column query dicts
    :type columns: list of dicts, or string

    :param csv: return a UTF8-encoded, CSV-formatted string instead of a list
        of dicts
    :type csv: bool

    :rtype: list of dicts, or CSV string

    """
    # Optionally read columns from file.
    if isinstance(columns, str):
        columns = _read_columns_file(columns)

    # Either "pattern" or "pattern_path" (but not both) is allowed in the
    # columns.json file, but "pattern" gets normalised to "pattern_path" here.
    for column in list(columns.values()):
        if "pattern" in column:
            assert "pattern_path" not in column, (
                'A column must have either a "pattern" or a "pattern_path"'
                "but not both")
            column["pattern_path"] = column["pattern"]
            del column["pattern"]


    table_ = []
    for d in dicts:
        row = collections.OrderedDict()  # The row we'll return in the table.
        for column_title, column_spec in list(columns.items()):
            if not column_spec.get('return_multiple_columns', False):
                row[column_title] = query(dict_=d, **column_spec)
            else:
                multiple_columns = query(dict_=d, **column_spec)
                for k, v in list(multiple_columns.items()):
                    row[k] = v

        table_.append(row)

    if pretty:
        # Return a pretty-printed string (looks like a nice table when printed
        # to stdout).
        return tabulate.tabulate(table_, tablefmt="grid", headers="keys")
    elif csv:
        # Return a string of CSV-formatted text.
        return _table_to_csv(table_)
    else:
        # Return a list of dicts.
        return table_


def query(pattern_path, dict_, max_length=None, strip=False,
          case_sensitive=False, unique=False, deduplicate=False,
          string_transformations=None, hyperlink=False,
          return_multiple_columns=False):
    """Query the given dict with the given pattern path and return the result.

    The ``pattern_path`` is a either a single regular expression string or a
    list of regex strings that will be matched against the keys of the dict and
    its subdicts to find the value(s) in the dict to return.

    The returned result is either a single value (None, "foo", 42, False...)
    or (if the pattern path matched multiple values in the dict) a list of
    values.

    If the dict contains sub-lists or sub-dicts values from these will be
    flattened into a simple flat list to be returned.

    """
    if string_transformations is None:
        string_transformations = []

    if max_length:
        string_transformations.append(lambda x: x[:max_length])

    if hyperlink:
        string_transformations.append(
            lambda x: '=HYPERLINK("{0}")'.format(x))

    if isinstance(pattern_path, str):
        pattern_path = [pattern_path]

    # Copy the pattern_path because we're going to modify it which can be
    # unexpected and confusing to user code.
    original_pattern_path = pattern_path
    pattern_path = pattern_path[:]

    # We're going to be popping strings off the end of the pattern path
    # (because Python lists don't come with a convenient pop-from-front method)
    # so we need the list in reverse order.
    pattern_path.reverse()

    result = _process_object(pattern_path, dict_,
                             string_transformations=string_transformations,
                             strip=strip, case_sensitive=case_sensitive,
                             return_multiple_columns=return_multiple_columns)

    if not result:
        return None  # Empty lists finally get turned into None.
    elif isinstance(result, dict):
        return _flatten(result)
    elif len(result) == 1:
        return result[0]  # One-item lists just get turned into the item.
    else:
        if unique:
            msg = "pattern_path: {0}\n\n".format(original_pattern_path)
            msg = msg + pprint.pformat(dict_)
            raise UniqueError(msg)
        if deduplicate:
            # Deduplicate the list while maintaining order.
            new_result = []
            for item in result:
                if item not in new_result:
                    new_result.append(item)
            result = new_result
        return result


def _flatten(d, parent_key='', sep='_'):
    items = []
    for k, v in list(d.items()):
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, collections.MutableMapping):
            items.extend(list(_flatten(v, new_key, sep=sep).items()))
        else:
            items.append((new_key, v))
    return collections.OrderedDict(items)


def _process_object(pattern_path, object_, **kwargs):
    if type(object_) in (tuple, list):
        return _process_list(pattern_path, object_, **kwargs)
    elif isinstance(object_, dict):
        return _process_dict(pattern_path, object_, **kwargs)
    elif isinstance(object_, str):
        return _process_string(object_, **kwargs)
    else:
        return [object_]


def _process_string(s, string_transformations=None, strip=False, **kwargs):
    if strip:
        s = s.strip()
    if string_transformations:
        for string_transformation in string_transformations:
            s = string_transformation(s)
    return [s]


def _process_list(pattern_path, list_, **kwargs):
    result = []
    for item in list_:
        result.extend(_process_object(pattern_path[0:], item, **kwargs))
    if pattern_path:
        pattern_path.pop()
    return result


def _process_dict(pattern_path, dict_, case_sensitive=False,
                  return_multiple_columns=False, **kwargs):

    result_dict = collections.OrderedDict()
    pattern = pattern_path.pop()

    if case_sensitive:
        flags = re.UNICODE
    else:
        flags = re.UNICODE | re.IGNORECASE
    regex = re.compile(pattern, flags)

    for key in dict_:
        if regex.search(key):
            result_dict[key] = _process_object(
                list(pattern_path), dict_[key],
                case_sensitive=case_sensitive,
                return_multiple_columns=return_multiple_columns,
                **kwargs
            )
    if not return_multiple_columns:
        return list(itertools.chain(*list(result_dict.values())))
    else:
        return result_dict
