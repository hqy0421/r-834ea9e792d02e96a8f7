"""
An editable many-to-one association view.

Orders refer to customers.  The flat detail sheet joins the two tables,
so a city can be corrected on any order row and a quantity can be
corrected on the detail sheet; both changes have to end up in the right
source row and every detail row of the same customer has to agree.
"""

import copy

import pytest

import pyexcel as pe
from pyexcel._compact import OrderedDict

ORDERS = [
    ["order_id", "customer_id", "qty"],
    ["A1", "C1", 5],
    ["A2", "C1", 2],
    ["A3", "C2", 7],
]

CUSTOMERS = [
    ["customer_id", "city"],
    ["C1", "Beijing"],
    ["C2", "Wuhan"],
]


def a_book(order_rows=None, customer_rows=None):
    """A book with the two source tables and one unrelated sheet"""
    orders = ORDERS if order_rows is None else order_rows
    customers = CUSTOMERS if customer_rows is None else customer_rows
    return pe.get_book(
        bookdict=OrderedDict(
            [
                ("orders", copy.deepcopy(orders)),
                ("customers", copy.deepcopy(customers)),
            ]
        )
    )


def a_view(order_rows=None, customer_rows=None):
    """A view of the standard book"""
    return pe.association_view(a_book(order_rows, customer_rows))


def a_detail(view):
    """The detail rows without the column names"""
    return [list(row) for row in view.array][1:]


def a_source(view):
    """The two source tables as arrays"""
    return (
        [list(row) for row in view.source_book["orders"].array],
        [list(row) for row in view.source_book["customers"].array],
    )


def a_state(view):
    """Everything an assignment could possibly change"""
    return (a_detail(view),) + a_source(view)


def test_the_detail_sheet_is_native():
    view = a_view()

    assert isinstance(view, pe.Sheet)
    assert view.colnames == ["order_id", "customer_id", "qty", "city"]
    assert view.array == [
        ["order_id", "customer_id", "qty", "city"],
        ["A1", "C1", 5, "Beijing"],
        ["A2", "C1", 2, "Beijing"],
        ["A3", "C2", 7, "Wuhan"],
    ]
    assert view.to_array() == view.array
    assert [record["city"] for record in view.records] == [
        "Beijing",
        "Beijing",
        "Wuhan",
    ]
    assert view.number_of_rows() == 3
    assert view.number_of_columns() == 4


def test_the_source_book_keeps_its_sheets_in_order():
    view = pe.association_view(
        pe.get_book(
            bookdict=OrderedDict(
                [
                    ("notes", [["remark"], ["a note"]]),
                    ("orders", copy.deepcopy(ORDERS)),
                    ("customers", copy.deepcopy(CUSTOMERS)),
                ]
            )
        )
    )

    assert view.source_book.sheet_names() == [
        "notes",
        "orders",
        "customers",
    ]
    assert view.source_book["orders"] is view.order_sheet
    assert view.source_book["customers"] is view.customer_sheet
    assert view.source_book["orders"].array == ORDERS
    assert view.source_book["customers"].array == CUSTOMERS


def test_the_input_book_is_left_alone():
    book = a_book()
    original = book.to_dict()

    view = pe.association_view(book)
    view[0, "city"] = "Shanghai"
    view[1, "qty"] = 22

    assert book.to_dict() == original
    assert book["customers"].array == CUSTOMERS
    assert book["orders"].array == ORDERS


def test_a_cell_can_be_set_by_name_and_by_index():
    view = a_view()

    view[0, "city"] = "Shanghai"
    assert view[0, "city"] == "Shanghai"
    assert view.customer_sheet.array == [
        ["customer_id", "city"],
        ["C1", "Shanghai"],
        ["C2", "Wuhan"],
    ]

    view[2, 2] = 70
    assert view.array[3] == ["A3", "C2", 70, "Wuhan"]
    assert view.order_sheet.array == [
        ["order_id", "customer_id", "qty"],
        ["A1", "C1", 5],
        ["A2", "C1", 2],
        ["A3", "C2", 70],
    ]


def test_a_cell_can_be_set_with_an_excel_position():
    view = a_view()

    view["D2"] = "Shenzhen"

    assert view.column["city"] == ["Shenzhen", "Shenzhen", "Wuhan"]


def test_a_whole_row_is_one_assignment():
    view = a_view()

    view.row[0] = ["A1", "C1", 11, "Nanjing"]

    assert a_detail(view) == [
        ["A1", "C1", 11, "Nanjing"],
        ["A2", "C1", 2, "Nanjing"],
        ["A3", "C2", 7, "Wuhan"],
    ]
    assert a_source(view) == (
        [
            ["order_id", "customer_id", "qty"],
            ["A1", "C1", 11],
            ["A2", "C1", 2],
            ["A3", "C2", 7],
        ],
        [
            ["customer_id", "city"],
            ["C1", "Nanjing"],
            ["C2", "Wuhan"],
        ],
    )


def test_a_whole_row_can_be_set_by_its_name():
    view = a_view()

    view.row[1] = ["A2", "C1", 2, "Suzhou"]

    assert a_detail(view)[0] == ["A1", "C1", 5, "Suzhou"]


def test_a_whole_column_is_one_assignment():
    view = a_view()

    view.column["qty"] = [50, 20, 70]

    assert [row[2] for row in a_detail(view)] == [50, 20, 70]
    assert a_source(view)[0] == [
        ["order_id", "customer_id", "qty"],
        ["A1", "C1", 50],
        ["A2", "C1", 20],
        ["A3", "C2", 70],
    ]


def test_a_customer_field_is_written_once_and_synced_to_every_order():
    view = a_view()

    view[0, "city"] = "Chengdu"

    assert a_detail(view) == [
        ["A1", "C1", 5, "Chengdu"],
        ["A2", "C1", 2, "Chengdu"],
        ["A3", "C2", 7, "Wuhan"],
    ]
    assert view.customer_sheet.array == [
        ["customer_id", "city"],
        ["C1", "Chengdu"],
        ["C2", "Wuhan"],
    ]


def test_the_same_value_twice_in_one_assignment_is_not_a_conflict():
    view = a_view()

    with view.transaction():
        view[0, "city"] = "Hefei"
        view[1, "city"] = "Hefei"

    assert view.customer_sheet.array == [
        ["customer_id", "city"],
        ["C1", "Hefei"],
        ["C2", "Wuhan"],
    ]


def test_a_changed_customer_reference_moves_the_customer_fields():
    view = a_view()

    view[0, "customer_id"] = "C2"

    assert a_detail(view)[0] == ["A1", "C2", 5, "Wuhan"]
    assert a_detail(view)[1] == ["A2", "C1", 2, "Beijing"]
    assert view.order_sheet.array[1] == ["A1", "C2", 5]


def test_a_customer_field_follows_a_reference_changed_at_the_same_time():
    view = a_view()

    with view.transaction():
        view[0, "customer_id"] = "C2"
        view[0, "city"] = "Hangzhou"

    assert a_detail(view) == [
        ["A1", "C2", 5, "Hangzhou"],
        ["A2", "C1", 2, "Beijing"],
        ["A3", "C2", 7, "Hangzhou"],
    ]
    assert view.customer_sheet.array == [
        ["customer_id", "city"],
        ["C1", "Beijing"],
        ["C2", "Hangzhou"],
    ]


def test_an_unknown_customer_reference_rejects_the_assignment():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        view.row[0] = ["A1", "C9", 5, "Beijing"]

    assert a_state(view) == before


def test_changing_the_order_key_rejects_the_assignment():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        view.row[0] = ["ZZ", "C1", 5, "Beijing"]

    assert a_state(view) == before


def test_a_contradiction_for_one_customer_field_rejects_the_assignment():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        with view.transaction():
            view[0, "city"] = "Shanghai"
            view[1, "city"] = "Shenzhen"

    assert a_state(view) == before


def test_a_contradiction_for_one_order_field_rejects_the_assignment():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        with view.transaction():
            view[0, "qty"] = 1
            view.order_sheet[0, "qty"] = 2

    assert a_state(view) == before


def test_a_contradiction_on_the_customer_reference_is_rejected():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        with view.transaction():
            view[0, "customer_id"] = "C2"
            view.order_sheet[0, "customer_id"] = "C1"

    assert a_state(view) == before


def test_an_out_of_range_cell_is_rejected():
    view = a_view()
    before = a_state(view)

    with pytest.raises(IndexError):
        view[3, 0] = "A4"

    with pytest.raises(IndexError):
        view[0, 4] = "too far"

    assert a_state(view) == before


def test_the_existing_rows_and_columns_cannot_grow():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        view.row += [["A4", "C1", 1, "Beijing"]]

    with pytest.raises(ValueError):
        view.column += [["qty2"]]

    with pytest.raises(ValueError):
        view.row[0] = ["A1", "C1", 5, "Beijing", "extra"]

    assert a_state(view) == before


def test_the_existing_rows_and_columns_cannot_shrink():
    view = a_view()
    before = a_state(view)

    with pytest.raises(ValueError):
        del view.row[0]

    with pytest.raises(ValueError):
        del view.column["city"]

    with pytest.raises(ValueError):
        view.filter(row_indices=[0])

    assert a_state(view) == before


def test_the_names_of_a_view_do_not_change():
    view = a_view()
    before = a_state(view)

    assert view.colnames == ["order_id", "customer_id", "qty", "city"]

    with pytest.raises(ValueError):
        view.name_columns_by_row(0)

    assert a_state(view) == before
    assert view.colnames == ["order_id", "customer_id", "qty", "city"]


def test_a_source_sheet_cell_can_be_set_directly():
    view = a_view()

    view.customer_sheet[0, "city"] = "Dalian"
    assert view.column["city"] == ["Dalian", "Dalian", "Wuhan"]

    view.order_sheet[0, "qty"] = 15
    assert a_detail(view)[0] == ["A1", "C1", 15, "Dalian"]


def test_the_source_book_is_the_authority():
    view = a_view()
    view[0, "city"] = "Qingdao"
    view[2, "qty"] = 71

    source = view.source_book

    assert source["customers"].array == [
        ["customer_id", "city"],
        ["C1", "Qingdao"],
        ["C2", "Wuhan"],
    ]
    assert source["orders"].array == [
        ["order_id", "customer_id", "qty"],
        ["A1", "C1", 5],
        ["A2", "C1", 2],
        ["A3", "C2", 71],
    ]
    assert list(source["customers"].records) == [
        OrderedDict([("customer_id", "C1"), ("city", "Qingdao")]),
        OrderedDict([("customer_id", "C2"), ("city", "Wuhan")]),
    ]


def test_the_source_book_and_the_view_save_the_same_values():
    view = a_view()
    view[0, "city"] = "Xiamen"
    view[1, "qty"] = 21

    detail = [list(row) for row in view.to_array()]
    source = view.source_book.to_dict()

    assert detail[1] == ["A1", "C1", 5, "Xiamen"]
    assert detail[2] == ["A2", "C1", 21, "Xiamen"]
    assert source["customers"] == [
        ["customer_id", "city"],
        ["C1", "Xiamen"],
        ["C2", "Wuhan"],
    ]
    assert source["orders"] == [
        ["order_id", "customer_id", "qty"],
        ["A1", "C1", 5],
        ["A2", "C1", 21],
        ["A3", "C2", 7],
    ]

    # every detail row is the join of the two source rows it points at
    shared = (
        source["customers"][0],
        source["customers"][1],
        source["customers"][2],
    )
    rows = source["orders"][1:]
    for order_row, detail_row in zip(rows, detail[1:]):
        customer_row = shared[
            1 + [r[0] for r in shared[1:]].index(order_row[1])
        ]
        assert detail_row == order_row + customer_row[1:]


def test_the_names_of_the_sheets_are_kept():
    view = a_view()

    assert view.order_sheet.name == "orders"
    assert view.customer_sheet.name == "customers"
    assert view.detail is view


def test_the_view_can_be_saved_and_reloaded(tmp_path):
    view = a_view()
    view[0, "city"] = "Lanzhou"

    target = tmp_path / "detail.csv"
    view.save_as(str(target))

    reloaded = pe.get_sheet(file_name=str(target))
    assert reloaded.array == view.to_array()


def test_an_unknown_customer_key_in_the_source_is_reported():
    with pytest.raises(ValueError):
        a_view(
            customer_rows=[
                ["customer_id", "city"],
                ["C1", "Beijing"],
                ["C1", "Wuhan"],
            ]
        )


def test_a_missing_parameter_is_reported():
    book = a_book()

    with pytest.raises(ValueError):
        pe.association_view(book, order_sheet="nothing")

    with pytest.raises(ValueError):
        pe.association_view(book, reference="nothing")

    with pytest.raises(ValueError):
        pe.association_view(book, unknown_parameter=True)


def test_the_sources_and_the_detail_can_be_saved_together(tmp_path):
    view = a_view()
    view[0, "city"] = "Yantai"

    together = view.to_book()

    assert together.sheet_names() == ["orders", "customers", "detail"]
    assert together["detail"] is view
    assert together["orders"] is view.order_sheet
    assert together["customers"] is view.customer_sheet
    assert together.to_dict()["customers"] == [
        ["customer_id", "city"],
        ["C1", "Yantai"],
        ["C2", "Wuhan"],
    ]

    target = tmp_path / "together.xlsx"
    together.save_as(str(target))
    reloaded = pe.get_book(file_name=str(target))
    assert reloaded.sheet_names() == ["orders", "customers", "detail"]
    assert reloaded["detail"].array == view.to_array()


def test_a_book_can_make_its_own_view():
    book = a_book()

    view = book.association_view()

    assert view.array == a_view().array
    view[0, "city"] = "Zhuhai"
    assert book["customers"].array == CUSTOMERS
    assert view.customer_sheet.array[1] == ["C1", "Zhuhai"]


def test_the_shapes_of_the_two_tables_are_joined():
    view = a_view()

    assert view.provenance["order_id"] == ("order", 0)
    assert view.provenance["customer_id"] == ("order", 1)
    assert view.provenance["qty"] == ("order", 2)
    assert view.provenance["city"] == ("customer", 1)


def test_a_source_sheet_with_column_names_can_be_used():
    orders = pe.Sheet(
        [["order_id", "customer_id", "qty"], ["A1", "C1", 5]],
        name_columns_by_row=0,
    )
    customers = pe.Sheet(
        [["customer_id", "city"], ["C1", "Beijing"]],
        name_columns_by_row=0,
    )
    book = pe.get_book(
        bookdict=OrderedDict([("orders", orders), ("customers", customers)])
    )

    view = pe.association_view(book)

    assert view.array == [
        ["order_id", "customer_id", "qty", "city"],
        ["A1", "C1", 5, "Beijing"],
    ]
    assert view.source_book["orders"].array == [
        ["order_id", "customer_id", "qty"],
        ["A1", "C1", 5],
    ]


def test_a_source_sheet_with_row_names_can_be_used():
    orders = pe.Sheet(
        [["order_id", "customer_id", "qty"], ["A1", "C1", 5]],
        name_columns_by_row=0,
    )
    orders.name_rows_by_column(0)
    customers = pe.Sheet(
        [["customer_id", "city"], ["C1", "Beijing"]],
        name_columns_by_row=0,
    )
    book = pe.get_book(
        bookdict=OrderedDict([("orders", orders), ("customers", customers)])
    )

    view = pe.association_view(book)

    assert view.order_key is None
    assert view.rownames == ["A1"]
    assert view.colnames == ["customer_id", "qty", "city"]
    assert a_detail(view) == [["A1", "C1", 5, "Beijing"]]

    view[0, "city"] = "Nanjing"
    assert view.customer_sheet.array == [
        ["customer_id", "city"],
        ["C1", "Nanjing"],
    ]
    assert view.order_sheet.array == [
        ["", "customer_id", "qty"],
        ["A1", "C1", 5],
    ]


def test_a_table_of_nothing_but_the_reference_can_change_it():
    book = pe.get_book(
        bookdict=OrderedDict(
            [
                ("orders", [["customer_id"], ["C1"]]),
                ("customers", copy.deepcopy(CUSTOMERS)),
            ]
        )
    )

    view = pe.association_view(book)

    # the reference is the only order column, so no order key is needed
    assert view.order_key is None

    view[0, "customer_id"] = "C2"
    assert view.array == [["customer_id", "city"], ["C2", "Wuhan"]]
    assert view.order_sheet.array == [["customer_id"], ["C2"]]


def test_a_column_name_shared_by_both_tables_is_made_unique():
    book = pe.get_book(
        bookdict=OrderedDict(
            [
                ("orders", [["id", "cid", "qty"], ["A1", "C1", 5]]),
                ("customers", [["cid", "qty", "city"], ["C1", 9, "B"]]),
            ]
        )
    )

    view = pe.association_view(book)

    assert view.colnames == ["id", "cid", "qty", "qty_2", "city"]
    assert view.array[1] == ["A1", "C1", 5, 9, "B"]

    view[0, "qty"] = 42
    assert view.order_sheet.array == [["id", "cid", "qty"], ["A1", "C1", 42]]
    assert view.customer_sheet.array == [
        ["cid", "qty", "city"],
        ["C1", 9, "B"],
    ]
