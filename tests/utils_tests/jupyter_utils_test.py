from cell_fixtures import *  # type: ignore

from clm.utils.jupyter_utils import (
    get_cell_type,
    is_code_cell,
    is_markdown_cell,
    get_tags,
    set_tags,
    has_tag,
    get_cell_language,
    is_deleted_cell,
    is_private_cell,
    is_public_cell,
    is_alternate_solution,
    is_answer_cell,
    get_slide_tag,
    is_cell_included_for_language,
    find_notebook_titles,
    is_starting_cell,
)


def test_get_cell_type(markdown_cell, code_cell):
    assert get_cell_type(markdown_cell) == 'markdown'
    assert get_cell_type(code_cell) == 'code'


def test_is_code_cell(markdown_cell, code_cell):
    assert not is_code_cell(markdown_cell)
    assert is_code_cell(code_cell)


def test_is_markdown_cell(markdown_cell, code_cell):
    assert is_markdown_cell(markdown_cell)
    assert not is_markdown_cell(code_cell)


def test_get_tags(code_cell, markdown_cell, markdown_slide_cell, kept_cell):
    assert get_tags(code_cell) == []
    assert get_tags(markdown_cell) == []
    assert get_tags(markdown_slide_cell) == ['slide']
    assert get_tags(kept_cell) == ['keep']


def test_get_tags_for_cell_with_multiple_tags(markdown_cell):
    set_tags(markdown_cell, ['tag1', 'tag2'])
    assert get_tags(markdown_cell) == ['tag1', 'tag2']


def test_set_tags_for_code_cell(code_cell):
    set_tags(code_cell, ['new_tag'])
    assert get_tags(code_cell) == ['new_tag']


def test_set_tags_for_markdown_cell(markdown_cell):
    set_tags(markdown_cell, ['new_tag'])
    assert get_tags(markdown_cell) == ['new_tag']


def test_set_tags_for_code_cell_with_existing_tags(kept_cell):
    set_tags(kept_cell, ['new_tag'])
    assert get_tags(kept_cell) == ['new_tag']


def test_set_tags_for_markdown_cell_with_existing_tags(markdown_slide_cell):
    set_tags(markdown_slide_cell, ['new_tag'])
    assert get_tags(markdown_slide_cell) == ['new_tag']


def test_has_tag_for_code_cell_without_tags(code_cell):
    assert not has_tag(code_cell, 'tag')


def test_has_tag_for_code_cell_with_tags(code_cell):
    set_tags(code_cell, ['my_tag', 'your_tag'])

    assert has_tag(code_cell, 'my_tag')
    assert has_tag(code_cell, 'your_tag')
    assert not has_tag(code_cell, 'their_tag')


def test_has_tag_for_markdown_cell_without_tags(markdown_cell):
    assert not has_tag(markdown_cell, 'tag')


def test_has_tag_for_markdown_cell_with_tags(markdown_cell):
    set_tags(markdown_cell, ['my_tag', 'your_tag'])

    assert has_tag(markdown_cell, 'my_tag')
    assert has_tag(markdown_cell, 'your_tag')
    assert not has_tag(markdown_cell, 'their_tag')


def test_get_cell_language_for_code_cell_without_language(code_cell):
    assert get_cell_language(code_cell) == ''


def test_get_cell_language_for_english_code_cell(english_code_cell):
    assert get_cell_language(english_code_cell) == 'en'


def test_get_cell_language_for_german_code_cell(german_code_cell):
    assert get_cell_language(german_code_cell) == 'de'


def test_is_deleted_cell_for_deleted_cell(deleted_cell):
    assert is_deleted_cell(deleted_cell)


def test_is_deleted_cell_for_non_deleted_cells(code_cell, markdown_cell):
    assert not is_deleted_cell(code_cell)
    assert not is_deleted_cell(markdown_cell)


def test_is_private_cell_for_private_cell(markdown_notes_cell):
    assert is_private_cell(markdown_notes_cell)


def test_is_private_cell_for_non_private_cells(code_cell, markdown_cell):
    assert not is_private_cell(code_cell)
    assert not is_private_cell(markdown_cell)


def test_is_public_cell_for_public_cells(code_cell, markdown_cell):
    assert is_public_cell(code_cell)
    assert is_public_cell(markdown_cell)


def test_is_public_cell_for_non_public_cell(markdown_notes_cell):
    assert not is_public_cell(markdown_notes_cell)


def test_is_starting_cell_for_starting_cell(starting_cell):
    assert is_starting_cell(starting_cell)


def test_is_starting_cell_for_non_starting_cell(code_cell, markdown_cell):
    assert not is_starting_cell(code_cell)
    assert not is_starting_cell(markdown_cell)


def test_is_alternate_solution_for_alternate_cell(alternate_cell):
    assert is_alternate_solution(alternate_cell)


def test_is_alternate_solution_for_non_alternate_cell(
    code_cell, markdown_cell
):
    assert not is_alternate_solution(code_cell)
    assert not is_alternate_solution(markdown_cell)


def test_is_answer_cell_for_answer_cell(answer_cell):
    assert is_answer_cell(answer_cell)


def test_is_answer_cell_for_code_cell(code_cell):
    assert is_answer_cell(code_cell)


def test_is_answer_cell_for_code_cell_with_keep_tag(kept_cell):
    assert not is_answer_cell(kept_cell)


def test_is_answer_cell_for_markdown_cell_without_tags(markdown_cell):
    assert not is_answer_cell(markdown_cell)


def test_is_answer_cell_for_markdown_cell_with_start_tag(starting_cell):
    assert not is_answer_cell(starting_cell)


def test_get_slide_tag(
    markdown_cell, markdown_slide_cell, markdown_subslide_cell
):
    assert get_slide_tag(markdown_cell) is None
    assert get_slide_tag(markdown_slide_cell) == 'slide'
    assert get_slide_tag(markdown_subslide_cell) == 'subslide'


def test_get_slide_tag_for_multiple_slide_tags(markdown_cell):
    markdown_cell.metadata['tags'] = ['slide', 'subslide']

    assert get_slide_tag(markdown_cell) in ['slide', 'subslide']


def test_is_cell_included_for_language_for_code_cell_without_language(
    code_cell,
):
    assert is_cell_included_for_language(code_cell, 'en')
    assert is_cell_included_for_language(code_cell, 'de')


def test_is_cell_included_for_language_for_english_code_cell(
    english_code_cell,
):
    assert is_cell_included_for_language(english_code_cell, 'en')
    assert not is_cell_included_for_language(english_code_cell, 'de')


def test_is_cell_included_for_language_for_german_code_cell(german_code_cell):
    assert not is_cell_included_for_language(german_code_cell, 'en')
    assert is_cell_included_for_language(german_code_cell, 'de')


def test_is_cell_included_for_language_for_markdown_cell(markdown_cell):
    assert is_cell_included_for_language(markdown_cell, 'en')
    assert is_cell_included_for_language(markdown_cell, 'de')


def test_is_cell_included_for_language_for_english_markdown_cell(
    english_markdown_cell,
):
    assert is_cell_included_for_language(english_markdown_cell, 'en')
    assert not is_cell_included_for_language(english_markdown_cell, 'de')


def test_is_cell_included_for_language_for_german_markdown_cell(
    german_markdown_cell,
):
    assert not is_cell_included_for_language(german_markdown_cell, 'en')
    assert is_cell_included_for_language(german_markdown_cell, 'de')


def test_find_notebook_titles_for_notebook_without_title():
    assert find_notebook_titles('Notebook without header.') == {
        'en': 'unnamed',
        'de': 'unnamed',
    }


def test_find_notebook_titles_for_notebook_with_title():
    assert find_notebook_titles('{{ header("Deutsch", "English") }}') == {
        'en': 'English',
        'de': 'Deutsch',
    }
    assert find_notebook_titles('{{header ( "Deutsch" ,"English" )}}') == {
        'en': 'English',
        'de': 'Deutsch',
    }


def test_find_notebook_titles_for_notebook_with_skipped_characters():
    assert find_notebook_titles('{{ header("A vs. B", "A vs. B") }}') == {
        'en': 'A vs B',
        'de': 'A vs B',
    }


def test_find_notebook_titles_for_notebook_with_replaced_characters():
    assert find_notebook_titles(
        '{{ header("See: <>?Here!%$", "{/a/b\\\\c/?}") }}'
    ) == {'en': '(_a_b__c_)', 'de': 'See __Here__'}
