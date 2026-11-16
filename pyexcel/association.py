"""
pyexcel.association
~~~~~~~~~~~~~~~~~~~

An editable many-to-one association view.

A view joins a "many" table (orders) with a "one" table (customers) into
one native :class:`pyexcel.Sheet`.  The view keeps independent copies of
the two source tables, so the book it was read from is left untouched.
Every column of the detail sheet remembers where it came from: an order
field is written back to the order row it belongs to, a customer field
is written back to the customer row and every detail row that refers to
that customer is refreshed.

One assignment is one transaction.  Contradictory values for one source
field, a reference to a customer that does not exist or a change of the
order key reject the whole assignment and leave the data untouched.

:copyright: (c) 2014-2026 by C Wang
:license: New BSD License, see LICENSE for more details
"""

import copy

from pyexcel import constants
from pyexcel.book import Book
from pyexcel.sheet import Sheet, make_names_unique
from pyexcel._compact import OrderedDict
from pyexcel.internal.sheets.row import Row as NamedRow
from pyexcel.internal.sheets.column import Column as NamedColumn
from pyexcel.internal.sheets.matrix import Matrix

ORDER = "order"
CUSTOMER = "customer"

MESSAGE_UNKNOWN_KEYWORD = "Unexpected parameter(s): %s"
MESSAGE_CONFLICTING_PARAMETER = (
    "Parameter '%s' was given twice with different values"
)
MESSAGE_BOOK_INVALID = "An association view needs an existing Book, not %s"
MESSAGE_SHEET_NOT_FOUND = "Sheet '%s' is not found in the book"
MESSAGE_SHEETS_REQUIRED = (
    "An association view needs one order sheet and one customer sheet"
)
MESSAGE_NO_COLUMNS = "Sheet '%s' has neither column names nor a header row"
MESSAGE_COLUMN_NOT_FOUND = "Column '%s' is not found in sheet '%s'"
MESSAGE_NO_REFERENCE = (
    "Please name the customer reference column: sheet '%s' and sheet "
    "'%s' share no column name"
)
MESSAGE_AMBIGUOUS_REFERENCE = (
    "Several column names can serve as the customer reference: %s"
)
MESSAGE_DUPLICATE_CUSTOMER = (
    "Customer key '%s' appears more than once in sheet '%s'"
)
MESSAGE_UNKNOWN_CUSTOMER = "Unknown customer reference: %s"
MESSAGE_ORDER_KEY = (
    "The order key column '%s' identifies the order source row and "
    "cannot be changed"
)
MESSAGE_CUSTOMER_KEY = (
    "The customer key column '%s' identifies the customer source row "
    "and cannot be changed"
)
MESSAGE_CONFLICT = (
    "Conflicting values are given for the same %s field '%s' in a single "
    "assignment"
)
MESSAGE_OUT_OF_RANGE = (
    "Cell (%s, %s) is outside the existing %d x %d table; an association "
    "view can only clean existing rows and columns"
)
MESSAGE_STRUCTURE = (
    "Cannot %s on an association view; only the cells of the existing "
    "detail rows can be changed"
)

ORDER_PARAMETERS = ("order_sheet", "order_table", "many_sheet", "many_table")
CUSTOMER_PARAMETERS = (
    "customer_sheet",
    "customer_table",
    "one_sheet",
    "one_table",
    "lookup_sheet",
)
REFERENCE_PARAMETERS = (
    "reference",
    "foreign_key",
    "reference_column",
    "join_on",
    "join_column",
)
KEY_PARAMETERS = ("order_key", "primary_key", "key_column")
NAME_PARAMETERS = ("name", "detail_name", "sheet_name")

MISSING = object()


class AssociationViewError(ValueError, KeyError, IndexError, TypeError):
    """One assignment was rejected as a whole; nothing was written

    It is a ``ValueError`` because a value was rejected, an
    ``IndexError`` because a cell may be outside the existing table and
    a ``TypeError`` because a change of the table shape is not
    supported.
    """

    __str__ = Exception.__str__


class _EmptyTransaction:
    """A stand-in when a sheet is not attached to a view yet"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _empty_transaction():
    return _EmptyTransaction()


class _Transaction:
    """Track the nesting depth of one assignment"""

    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        self.owner._enter_transaction()
        return self.owner

    def __exit__(self, exc_type, exc_value, traceback):
        self.owner._leave_transaction(exc_type, exc_value, traceback)
        return False


def _as_a_list(value):
    """Treat one value as a list of one value

    A plain string is one cell, not a series of characters.
    """
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "__iter__"):
        return list(value)
    return [value]


def _unique_name(name, taken):
    """Return a name that is not in the taken list yet"""
    candidate = name
    suffix = 1
    while candidate in taken:
        suffix += 1
        candidate = f"{name}_{suffix}"
    return candidate


def _prepare(sheet):
    """Split a sheet into its data rows, column names and row names

    The returned array is an independent copy without a header row.  A
    sheet that has no column names yet is supposed to have its header
    on the first row.
    """
    array = copy.deepcopy(sheet.get_internal_array())
    colnames = list(sheet.colnames)
    rownames = list(sheet.rownames)
    if len(colnames) == 0:
        if len(rownames) > 0 or len(array) == 0:
            raise AssociationViewError(MESSAGE_NO_COLUMNS % sheet.name)
        colnames = make_names_unique(array[0])
        array = array[1:]
    if len(colnames) == 0:
        raise AssociationViewError(MESSAGE_NO_COLUMNS % sheet.name)
    width = len(colnames)
    normalised = []
    for row in array:
        if len(row) > width:
            raise AssociationViewError(MESSAGE_NO_COLUMNS % sheet.name)
        normalised.append(
            list(row) + [constants.DEFAULT_NA] * (width - len(row))
        )
    return normalised, colnames, rownames


class _EditableSheet(Sheet):
    """A sheet whose changes are cell level and atomic

    Structural changes are refused because a view keeps the rows, the
    columns and the names it was built with.
    """

    _locked = False

    def init(self, *args, **keywords):
        """Keep the constructor work but install transactional rows"""
        if self._locked:
            raise AssociationViewError(MESSAGE_STRUCTURE % "re-initialise")
        Sheet.init(self, *args, **keywords)
        self.row = _EditableRow(self)
        self.column = _EditableColumn(self)

    # -- transactions -----------------------------------------------------

    def transaction(self):
        """Open one assignment; nested calls join the outer one"""
        return _empty_transaction()

    # -- sizes ------------------------------------------------------------

    def _row_limit(self):
        """How many rows may be written to"""
        return self.number_of_rows()

    def _column_limit(self):
        """How many columns may be written to"""
        return self.number_of_columns()

    # -- cell writes ------------------------------------------------------

    def _locate(self, row, column):
        """Resolve one existing cell, refusing anything outside the table"""
        rows = self._row_limit()
        columns = self._column_limit()
        if isinstance(row, bool) or not isinstance(row, int):
            raise AssociationViewError(
                MESSAGE_OUT_OF_RANGE % (row, column, rows, columns)
            )
        if isinstance(column, bool) or not isinstance(column, int):
            raise AssociationViewError(
                MESSAGE_OUT_OF_RANGE % (row, column, rows, columns)
            )
        if row < 0:
            row = row + rows
        if column < 0:
            column = column + columns
        if row < 0 or row >= rows or column < 0 or column >= columns:
            raise AssociationViewError(
                MESSAGE_OUT_OF_RANGE % (row, column, rows, columns)
            )
        return row, column

    def _stage_cell(self, row, column, value):
        """Put one cell change into the current assignment"""
        raise AssociationViewError(MESSAGE_STRUCTURE % "assign")

    def cell_value(self, row, column, new_value=None):
        """Random access to table cells

        Reading is delegated to the matrix.  Writing is staged so that
        the whole assignment can be rejected without partial writes.
        """
        if new_value is None:
            return Matrix.cell_value(self, row, column)
        row, column = self._locate(row, column)
        with self.transaction():
            self._stage_cell(row, column, new_value)

    def set_row_at(self, row_index, data_array):
        """Update one existing row without adding or dropping columns"""
        row, _ = self._locate(row_index, 0)
        columns = self._column_limit()
        values = _as_a_list(data_array)
        if len(values) > columns:
            raise AssociationViewError(
                MESSAGE_STRUCTURE % "assign a row that is too long"
            )
        with self.transaction():
            for column in range(columns):
                if column < len(values):
                    value = values[column]
                else:
                    value = constants.DEFAULT_NA
                self._stage_cell(row, column, value)

    def _set_row_at(self, row_index, data_array, starting=0):
        """Update part of one existing row"""
        row, start = self._locate(row_index, starting)
        columns = self._column_limit()
        values = _as_a_list(data_array)
        if start + len(values) > columns:
            raise AssociationViewError(
                MESSAGE_STRUCTURE % "assign outside the existing columns"
            )
        with self.transaction():
            for offset, value in enumerate(values):
                self._stage_cell(row, start + offset, value)

    def set_column_at(self, column_index, data_array, starting=0):
        """Update part of one existing column without adding rows"""
        start, column = self._locate(starting, column_index)
        rows = self._row_limit()
        values = _as_a_list(data_array)
        if start + len(values) > rows:
            raise AssociationViewError(
                MESSAGE_STRUCTURE % "assign outside the existing rows"
            )
        with self.transaction():
            for offset, value in enumerate(values):
                self._stage_cell(start + offset, column, value)

    def map(self, custom_function):
        """Apply one function to every cell in a single assignment"""
        with self.transaction():
            Matrix.map(self, custom_function)

    # -- refused structural changes ---------------------------------------

    def _deny(self, operation):
        raise AssociationViewError(MESSAGE_STRUCTURE % operation)

    def extend_rows(self, rows):
        """Rows cannot be appended to a view"""
        self._deny("add rows")

    def _extend_row(self, row):
        self._deny("add rows")

    def extend_columns(self, columns):
        """Columns cannot be appended to a view"""
        self._deny("add columns")

    def _extend_columns_with_rows(self, rows):
        self._deny("add columns")

    def extend_columns_with_rows(self, rows):
        """Columns cannot be appended to a view"""
        self._deny("add columns")

    def delete_rows(self, row_indices):
        """Rows cannot be removed from a view"""
        self._deny("remove rows")

    def delete_columns(self, column_indices):
        """Columns cannot be removed from a view"""
        self._deny("remove columns")

    def delete_named_row_at(self, name):
        """Rows cannot be removed from a view"""
        self._deny("remove rows")

    def delete_named_column_at(self, name):
        """Columns cannot be removed from a view"""
        self._deny("remove columns")

    def filter(self, column_indices=None, row_indices=None):
        """A view cannot drop rows or columns"""
        self._deny("filter rows or columns")

    def select(self, indices):
        """A view cannot drop rows or columns"""
        self._deny("filter rows or columns")

    def paste(self, topleft_corner, rows=None, columns=None):
        """A view cannot grow by pasting"""
        self._deny("paste")

    def cut(self, topleft_corner, bottomright_corner):
        """A view cannot be cut apart"""
        self._deny("cut")

    def transpose(self):
        """A transposed view would lose its field provenance"""
        self._deny("transpose")

    def __iadd__(self, other):
        """A view cannot be combined in place"""
        self._deny("combine sheets")

    def __add__(self, other):
        """A view cannot be combined into another book"""
        self._deny("combine sheets")

    def name_columns_by_row(self, row_index):
        """A view keeps the column names it was built with"""
        self._deny("rename columns")

    def name_rows_by_column(self, column_index):
        """A view keeps the row names it was built with"""
        self._deny("rename rows")

    @property
    def colnames(self):
        """Return the column names of the view"""
        return Sheet.colnames.fget(self)

    @colnames.setter
    def colnames(self, value):
        """A locked sheet keeps the column names it was built with"""
        if self._locked:
            self._deny("rename columns")
        Sheet.colnames.fset(self, value)

    @property
    def rownames(self):
        """Return the row names of the view"""
        return Sheet.rownames.fget(self)

    @rownames.setter
    def rownames(self, value):
        """A locked sheet keeps the row names it was built with"""
        if self._locked:
            self._deny("rename rows")
        Sheet.rownames.fset(self, value)

    # -- copying ----------------------------------------------------------

    def clone(self):
        """Return an independent plain copy of this sheet"""
        new_sheet = Sheet(
            copy.deepcopy(self.get_internal_array()), name=self.name
        )
        if len(self.colnames) > 0:
            new_sheet.colnames = list(self.colnames)
        if len(self.rownames) > 0:
            new_sheet.rownames = list(self.rownames)
        return new_sheet

    def __deepcopy__(self, memo):
        return self.clone()


class _EditableRow(NamedRow):
    """Make one row assignment one transaction"""

    def __setitem__(self, aslice, a_row):
        with self._ref.transaction():
            NamedRow.__setitem__(self, aslice, a_row)

    def format(self, row_index=None, formatter=None, format_specs=None):
        """Format rows as one assignment"""
        with self._ref.transaction():
            NamedRow.format(self, row_index, formatter, format_specs)


class _EditableColumn(NamedColumn):
    """Make one column assignment one transaction"""

    def __setitem__(self, aslice, a_column):
        with self._ref.transaction():
            NamedColumn.__setitem__(self, aslice, a_column)

    def format(self, column_index=None, formatter=None, format_specs=None):
        """Format columns as one assignment"""
        with self._ref.transaction():
            NamedColumn.format(self, column_index, formatter, format_specs)


class SourceSheet(_EditableSheet):
    """An independent copy of one source table

    The copy presents the same column names, row names and array as the
    sheet it was copied from.  A change made here is reported to the
    view that owns the copy, so that the detail rows are refreshed at
    the end of the assignment.
    """

    def __init__(self, source):
        array, colnames, rownames = _prepare(source)
        self._controller = None
        self._role = None
        Sheet.__init__(self, array, name=source.name)
        self.colnames = colnames
        if len(rownames) > 0:
            self.rownames = rownames
        self._locked = True

    def attach(self, view, role):
        """Report changes to the owning view"""
        self._controller = view
        self._role = role

    def transaction(self):
        """Share the transaction of the owning view"""
        controller = self._controller
        if controller is None:
            return _empty_transaction()
        return controller.transaction()

    def _stage_cell(self, row, column, value):
        controller = self._controller
        if controller is None:
            Matrix.cell_value(self, row, column, value)
        else:
            controller.stage_source(self._role, row, column, value)


class AssociationView(_EditableSheet):
    """An editable many-to-one association view

    The view is itself the native detail sheet.  It joins an order
    sheet with a customer sheet on a shared column name.  Order columns
    are written back to the order row the detail row was built from and
    customer columns are written back to the customer row the order
    refers to.

    Example::

        >>> import pyexcel as pe
        >>> from pyexcel._compact import OrderedDict
        >>> book = pe.get_book(bookdict=OrderedDict([
        ...     ("orders", [
        ...         ["order_id", "customer_id", "qty"],
        ...         ["A1", "C1", 5],
        ...         ["A2", "C1", 2],
        ...     ]),
        ...     ("customers", [
        ...         ["customer_id", "city"],
        ...         ["C1", "Beijing"],
        ...     ]),
        ... ]))
        >>> view = pe.association_view(book)
        >>> view.column["city"]
        ['Beijing', 'Beijing']
        >>> view[0, "city"] = "Shanghai"
        >>> view.column["city"]
        ['Shanghai', 'Shanghai']
        >>> view.source_book["customers"].array
        [['customer_id', 'city'], ['C1', 'Shanghai']]
        >>> book["customers"].array
        [['customer_id', 'city'], ['C1', 'Beijing']]

    """

    def __init__(
        self,
        book,
        order_sheet=None,
        customer_sheet=None,
        reference=None,
        order_key=None,
        name=None,
        **keywords,
    ):
        order_sheet = _alias(keywords, ORDER_PARAMETERS, order_sheet)
        customer_sheet = _alias(keywords, CUSTOMER_PARAMETERS, customer_sheet)
        reference = _alias(keywords, REFERENCE_PARAMETERS, reference)
        order_key = _alias(keywords, KEY_PARAMETERS, order_key)
        name = _alias(keywords, NAME_PARAMETERS, name)
        if keywords:
            raise AssociationViewError(
                MESSAGE_UNKNOWN_KEYWORD % ", ".join(sorted(keywords))
            )

        book = _to_book(book)
        order_name, customer_name = _resolve_sheets(
            book, order_sheet, customer_sheet
        )
        order_source = book[order_name]
        customer_source = book[customer_name]
        order_array, order_columns, order_rows = _prepare(order_source)
        customer_array, customer_columns, _ = _prepare(customer_source)

        reference = _resolve_reference(
            reference,
            order_name,
            order_columns,
            customer_name,
            customer_columns,
        )
        order_key = _resolve_order_key(
            order_key, order_name, order_columns, reference, order_rows
        )
        provenance = _resolve_provenance(
            reference, order_columns, customer_columns
        )
        detail_columns = [item[0] for item in provenance]

        self._depth = 0
        self._detail_staged = OrderedDict()
        self._source_staged = OrderedDict()
        self._reference = reference
        self._order_key = order_key
        self._provenance = [(item[1], item[2]) for item in provenance]
        self._customer_index = _index_customers(
            customer_array, customer_columns, reference, customer_name
        )

        detail = _join(
            order_array,
            provenance,
            order_columns.index(reference),
            customer_array,
            self._customer_index,
        )
        Sheet.__init__(self, detail, name="detail" if name is None else name)
        self.colnames = detail_columns
        if len(order_rows) > 0:
            self.rownames = order_rows

        self._order = SourceSheet(order_source)
        self._customer = SourceSheet(customer_source)
        self._order.attach(self, ORDER)
        self._customer.attach(self, CUSTOMER)
        self._source_book = _copy_book(
            book,
            order_name,
            customer_name,
            self._order,
            self._customer,
        )
        self._locked = True

    # -- what the view exposes --------------------------------------------

    @property
    def source_book(self):
        """The independent copy of the source tables"""
        return self._source_book

    @property
    def book(self):
        """An alias of :attr:`source_book`"""
        return self._source_book

    @property
    def detail(self):
        """The detail sheet, which is the view itself"""
        return self

    @property
    def detail_sheet(self):
        """An alias of :attr:`detail`"""
        return self

    @property
    def source(self):
        """An alias of :attr:`source_book`"""
        return self._source_book

    @property
    def order_sheet(self):
        """The independent copy of the order source table"""
        return self._order

    @property
    def customer_sheet(self):
        """The independent copy of the customer source table"""
        return self._customer

    @property
    def reference(self):
        """The name of the column that refers to a customer"""
        return self._reference

    @property
    def order_key(self):
        """The name of the column that identifies an order"""
        return self._order_key

    @property
    def provenance(self):
        """For each detail column, the table and the column it comes from"""
        result = OrderedDict()
        for index, (table, column) in enumerate(self._provenance):
            result[self.colnames[index]] = (table, column)
        return result

    def to_book(self):
        """The two source copies plus the detail sheet in one book

        The source sheets are the very objects :attr:`source_book`
        holds and the detail sheet is this view, so saving the book and
        saving the pieces separately produce the same values.
        """
        content = OrderedDict()
        taken = []
        for name in self._source_book.sheet_names():
            content[name] = self._source_book[name]
            taken.append(name)
        detail_name = _unique_name(self.name, taken)
        content[detail_name] = self
        return Book(content)

    # -- transactions -----------------------------------------------------

    def transaction(self):
        """Open one assignment; nested calls join the outer one"""
        return _Transaction(self)

    def _enter_transaction(self):
        self._depth += 1

    def _leave_transaction(self, exc_type, exc_value, traceback):
        self._depth -= 1
        if self._depth > 0:
            return
        if exc_type is not None:
            self._detail_staged = OrderedDict()
            self._source_staged = OrderedDict()
            return
        if self._detail_staged or self._source_staged:
            detail = self._detail_staged
            source = self._source_staged
            self._detail_staged = OrderedDict()
            self._source_staged = OrderedDict()
            self._apply(detail, source)

    # -- staging ----------------------------------------------------------

    def _stage_cell(self, row, column, value):
        key = (row, column)
        existing = self._detail_staged.get(key, MISSING)
        if existing is not MISSING and existing != value:
            raise AssociationViewError(
                MESSAGE_CONFLICT % (self.name, self.colnames[column])
            )
        self._detail_staged[key] = value

    def stage_source(self, table, row, column, value):
        """Stage one change made directly on a source copy"""
        key = (table, row, column)
        existing = self._source_staged.get(key, MISSING)
        if existing is not MISSING and existing != value:
            sheet = self._order if table == ORDER else self._customer
            raise AssociationViewError(
                MESSAGE_CONFLICT % (sheet.name, sheet.colnames[column])
            )
        self._source_staged[key] = value

    # -- applying ---------------------------------------------------------

    def _apply(self, detail_staged, source_staged):
        order = self._order
        customer = self._customer
        order_columns = order.colnames
        customer_columns = customer.colnames
        reference_column = order_columns.index(self._reference)
        order_key_column = None
        if self._order_key is not None:
            order_key_column = order_columns.index(self._order_key)
        customer_key_column = customer_columns.index(self._reference)
        order_rows = order.number_of_rows()
        customer_rows = customer.number_of_rows()

        writes = OrderedDict()

        def record(table, row, column, value, field):
            key = (table, row, column)
            existing = writes.get(key, MISSING)
            if existing is not MISSING and existing != value:
                name = order.name if table == ORDER else customer.name
                raise AssociationViewError(MESSAGE_CONFLICT % (name, field))
            writes[key] = value

        # 1. which orders point at a different customer now?
        new_reference = OrderedDict()
        for (row, column), value in detail_staged.items():
            table, source_column = self._provenance[column]
            if table != ORDER or source_column != reference_column:
                continue
            if value != Matrix.cell_value(order, row, reference_column):
                new_reference[row] = value
        for (table, row, column), value in source_staged.items():
            if table != ORDER or column != reference_column:
                continue
            if row >= order_rows or row < 0:
                raise AssociationViewError(
                    MESSAGE_OUT_OF_RANGE
                    % (row, column, order_rows, order.number_of_columns())
                )
            if value == Matrix.cell_value(order, row, column):
                continue
            known = new_reference.get(row, MISSING)
            if known is not MISSING and known != value:
                raise AssociationViewError(
                    MESSAGE_CONFLICT % (order.name, self._reference)
                )
            new_reference[row] = value
        for value in new_reference.values():
            if value not in self._customer_index:
                raise AssociationViewError(MESSAGE_UNKNOWN_CUSTOMER % (value,))

        # 2. turn every detail change into a source field change
        for (row, column), value in detail_staged.items():
            table, source_column = self._provenance[column]
            field = self.colnames[column]
            if table == ORDER:
                record(table, row, source_column, value, field)
            else:
                reference = new_reference.get(
                    row, Matrix.cell_value(order, row, reference_column)
                )
                customer_row = self._customer_index.get(reference)
                if customer_row is None:
                    raise AssociationViewError(
                        MESSAGE_UNKNOWN_CUSTOMER % (reference,)
                    )
                record(table, customer_row, source_column, value, field)

        # 3. turn every direct source change into a source field change
        for (table, row, column), value in source_staged.items():
            if table == ORDER:
                sheet = order
                rows = order_rows
                field = order_columns[column]
            else:
                sheet = customer
                rows = customer_rows
                field = customer_columns[column]
            if row >= rows or row < 0:
                raise AssociationViewError(
                    MESSAGE_OUT_OF_RANGE
                    % (row, column, rows, sheet.number_of_columns())
                )
            record(table, row, column, value, field)

        # 4. the two keys identify the source rows and cannot change
        for (table, row, column), value in writes.items():
            if table == ORDER and column == order_key_column:
                if value != Matrix.cell_value(order, row, column):
                    raise AssociationViewError(
                        MESSAGE_ORDER_KEY % self._order_key
                    )
            if table == CUSTOMER and column == customer_key_column:
                if value != Matrix.cell_value(customer, row, column):
                    raise AssociationViewError(
                        MESSAGE_CUSTOMER_KEY % self._reference
                    )

        # 5. everything is valid: write it down and refresh the detail
        for (table, row, column), value in writes.items():
            sheet = order if table == ORDER else customer
            Matrix.cell_value(sheet, row, column, value)
        self._refresh()

    def _refresh(self):
        """Recompose every detail cell from the two source tables"""
        order_array = self._order.get_internal_array()
        customer_array = self._customer.get_internal_array()
        reference_column = self._order.colnames.index(self._reference)
        index = self._customer_index
        for row in range(self.number_of_rows()):
            order_row = order_array[row]
            customer_row = index.get(order_row[reference_column])
            for column, (table, source_column) in enumerate(self._provenance):
                if table == ORDER:
                    value = order_row[source_column]
                elif customer_row is None:
                    value = constants.DEFAULT_NA
                else:
                    value = customer_array[customer_row][source_column]
                Matrix.cell_value(self, row, column, value)


def _alias(keywords, names, explicit):
    """Take one parameter from its aliases"""
    found = explicit
    for name in names:
        if name in keywords:
            value = keywords.pop(name)
            if found is not None and found != value:
                raise AssociationViewError(
                    MESSAGE_CONFLICTING_PARAMETER % name
                )
            found = value
    return found


def _to_book(book):
    """Accept a book or a dictionary a book can be built from"""
    if isinstance(book, Book):
        return book
    if isinstance(book, dict):
        return Book(OrderedDict(book))
    raise AssociationViewError(MESSAGE_BOOK_INVALID % type(book).__name__)


def _resolve_sheets(book, order_sheet, customer_sheet):
    """Find the two source sheets in a book"""
    names = book.sheet_names()
    if order_sheet is not None and order_sheet not in names:
        raise AssociationViewError(MESSAGE_SHEET_NOT_FOUND % (order_sheet,))
    if customer_sheet is not None and customer_sheet not in names:
        raise AssociationViewError(MESSAGE_SHEET_NOT_FOUND % (customer_sheet,))
    if order_sheet is None and customer_sheet is None:
        candidates = _candidate_pairs(book)
        if len(candidates) == 1:
            return candidates[0]
        if len(names) == 2:
            return names[0], names[1]
        raise AssociationViewError(MESSAGE_SHEETS_REQUIRED)
    if order_sheet is None:
        remaining = [name for name in names if name != customer_sheet]
        if len(remaining) != 1:
            raise AssociationViewError(MESSAGE_SHEETS_REQUIRED)
        return remaining[0], customer_sheet
    if customer_sheet is None:
        remaining = [name for name in names if name != order_sheet]
        if len(remaining) != 1:
            raise AssociationViewError(MESSAGE_SHEETS_REQUIRED)
        return order_sheet, remaining[0]
    if order_sheet == customer_sheet:
        raise AssociationViewError(MESSAGE_SHEETS_REQUIRED)
    return order_sheet, customer_sheet


def _candidate_pairs(book):
    """Pairs of sheets that may be joined many to one

    Two sheets qualify when they share a column.  The sheet in which the
    shared column holds unique values is the "one" side, so it is
    returned second; when both sides are unique (or neither is), the
    order the sheets appear in the book decides.
    """
    tables = OrderedDict()
    for name in book.sheet_names():
        try:
            array, colnames, _ = _prepare(book[name])
        except AssociationViewError:
            continue
        tables[name] = (colnames, array)
    names = list(tables.keys())
    candidates = []
    for first in range(0, len(names)):
        for second in range(first + 1, len(names)):
            left = names[first]
            right = names[second]
            left_columns, left_array = tables[left]
            right_columns, right_array = tables[right]
            shared = [name for name in left_columns if name in right_columns]
            if len(shared) == 0:
                continue
            unique_left = {
                name
                for name in shared
                if _is_unique(left_columns, left_array, name)
            }
            unique_right = {
                name
                for name in shared
                if _is_unique(right_columns, right_array, name)
            }
            if unique_right and not unique_left:
                candidates.append((left, right))
            elif unique_left and not unique_right:
                candidates.append((right, left))
            else:
                # a one-to-one pair: the book order decides
                candidates.append((left, right))
    return candidates


def _is_unique(colnames, array, name):
    """Check that a column holds a value only once"""
    column = colnames.index(name)
    seen = set()
    for row in array:
        if len(row) <= column:
            return False
        value = row[column]
        if value in seen:
            return False
        seen.add(value)
    return True


def _resolve_reference(
    reference,
    order_name,
    order_columns,
    customer_name,
    customer_columns,
):
    """Find the column that turns an order into a customer"""
    if reference is None:
        shared = [name for name in order_columns if name in customer_columns]
        if len(shared) == 0:
            raise AssociationViewError(
                MESSAGE_NO_REFERENCE % (order_name, customer_name)
            )
        if len(shared) > 1:
            preferred = [
                name for name in shared if name == customer_columns[0]
            ]
            if len(preferred) != 1:
                raise AssociationViewError(
                    MESSAGE_AMBIGUOUS_REFERENCE % (", ".join(shared),)
                )
            reference = preferred[0]
        else:
            reference = shared[0]
    if reference not in order_columns:
        raise AssociationViewError(
            MESSAGE_COLUMN_NOT_FOUND % (reference, order_name)
        )
    if reference not in customer_columns:
        raise AssociationViewError(
            MESSAGE_COLUMN_NOT_FOUND % (reference, customer_name)
        )
    return reference


def _resolve_order_key(
    order_key, order_name, order_columns, reference, rownames
):
    """Find the column that identifies an order

    The first column that is not the customer reference is the key by
    default, because the reference can change while the key cannot.
    When the order sheet uses row names, the key is not a cell at all.
    """
    if order_key is not None:
        if order_key not in order_columns:
            raise AssociationViewError(
                MESSAGE_COLUMN_NOT_FOUND % (order_key, order_name)
            )
        return order_key
    if len(rownames) > 0:
        return None
    for name in order_columns:
        if name != reference:
            return name
    # the only column is the reference itself: nothing identifies an order
    # besides the row it lives in, so a reference change stays allowed
    return None


def _resolve_provenance(reference, order_columns, customer_columns):
    """Pair every detail column with the source column it comes from"""
    provenance = []
    taken = []
    for column, name in enumerate(order_columns):
        taken.append(name)
        provenance.append((name, ORDER, column))
    for column, name in enumerate(customer_columns):
        if name == reference:
            continue
        taken.append(_unique_name(name, taken))
        provenance.append((taken[-1], CUSTOMER, column))
    return provenance


def _index_customers(customer_array, customer_columns, reference, name):
    """Map every customer key to its row"""
    key_column = customer_columns.index(reference)
    index = OrderedDict()
    for row, values in enumerate(customer_array):
        key = values[key_column]
        if key in index and index[key] != row:
            raise AssociationViewError(
                MESSAGE_DUPLICATE_CUSTOMER % (key, name)
            )
        index[key] = row
    return index


def _join(
    order_array, provenance, reference_column, customer_array, customer_index
):
    """Build the detail rows out of the two source tables"""
    detail = []
    for order_row in order_array:
        customer_row = customer_index.get(order_row[reference_column])
        values = []
        for _, table, source_column in provenance:
            if table == ORDER:
                values.append(order_row[source_column])
            elif customer_row is None:
                values.append(constants.DEFAULT_NA)
            else:
                values.append(customer_array[customer_row][source_column])
        detail.append(values)
    return detail


def _copy_book(book, order_name, customer_name, order_copy, customer_copy):
    """Copy every sheet, keeping the order of the book that was read

    The order sheet and the customer sheet become the live source
    copies; the remaining sheets are independent plain copies.
    """
    copies = {order_name: order_copy, customer_name: customer_copy}
    content = OrderedDict()
    for name in book.sheet_names():
        if name in copies:
            content[name] = copies[name]
        else:
            content[name] = _copy_sheet(book[name])
    return Book(content)


def _copy_sheet(sheet):
    """An independent plain copy of a sheet

    The copy keeps the array, the column names and the row names of the
    sheet, so that it presents the same values when saved.
    """
    copy_of_sheet = Sheet(
        copy.deepcopy(sheet.get_internal_array()), name=sheet.name
    )
    if len(sheet.colnames) > 0:
        copy_of_sheet.colnames = list(sheet.colnames)
    if len(sheet.rownames) > 0:
        copy_of_sheet.rownames = list(sheet.rownames)
    return copy_of_sheet


def get_association_view(book, **keywords):
    """Read an existing book and return an editable association view

    :param book: an existing :class:`pyexcel.Book`
    :param order_sheet: the name of the "many" sheet
    :param customer_sheet: the name of the "one" sheet
    :param reference: the shared column name that refers to a customer
    :param order_key: the column that identifies an order
    :param name: the name of the detail sheet
    :returns: an :class:`AssociationView`, which is a native sheet
    """
    return AssociationView(book, **keywords)


association_view = get_association_view
