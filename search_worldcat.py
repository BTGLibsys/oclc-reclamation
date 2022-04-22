import argparse
import dotenv
import libraries.record
import libraries.records_buffer
import logging
import logging.config
import numpy as np
import os
import pandas as pd
from datetime import datetime
from json.decoder import JSONDecodeError
from requests.exceptions import HTTPError

dotenv_file = dotenv.find_dotenv()
dotenv.load_dotenv(dotenv_file)

logger = logging.getLogger(__name__)


def init_argparse() -> argparse.ArgumentParser:
    """Initializes and returns ArgumentParser object."""

    parser = argparse.ArgumentParser(
        description=("For each row in the input file, find the record's OCLC "
            "Number by searching WorldCat using the available record "
            "identifiers. Script results are saved to the following directory: "
            "outputs/search_worldcat/")
    )
    parser.add_argument(
        '-v', '--version', action='version',
        version=f'{parser.prog} version 1.0.0'
    )
    parser.add_argument(
        'input_file',
        type=str,
        help=('the name and path of the input file, which must be in either '
            'CSV (.csv) or Excel (.xlsx or .xls) format (e.g. inputs/'
            'search_worldcat/filename.csv)')
    )
    parser.add_argument(
        '--search_my_library_holdings_first',
        action='store_true',
        help=("whether to first search WorldCat for your library's holdings. "
            'Use this option if you want to search in the following order: '
            '1) Search with "held by" filter. '
            '2) If there are no WorldCat search results held by your library, '
            'then search without "held by" filter. '
            'Without this option, the default search order is as follows: '
            '1) Search without "held by" filter. '
            '2) If there is more than one WorldCat search result, then search '
            'with "held by" filter to narrow down the results.')
    )
    return parser


def main() -> None:
    """Searches WorldCat for each record in input file and saves OCLC Number.

    For each row in the input file, a WorldCat search is performed using the
    first available record identifier (in this order):
    - lccn_fixed (i.e. a corrected version of the lccn value)
    - lccn
    - isbn (accepts multiple values separated by a semicolon)
    - issn (accepts multiple values separated by a semicolon)
    - gov_doc_class_num_086 (i.e. MARC field 086: Government Document
      Classification Number): If the gpo_item_num_074 (i.e. MARC field 074:
      GPO Item Number) is also available, then a combined search is
      performed (gov_doc_class_num_086 AND gpo_item_num_074). If only
      gpo_item_num_074 is available, then no search is performed.

    Outputs the following files:
    - outputs/search_worldcat/records_with_oclc_num.csv
        Records with one WorldCat match; hence, the OCLC Number has been found
    - outputs/search_worldcat/records_with_zero_or_multiple_worldcat_matches.csv
        Records whose search returned zero or multiple WorldCat matches
    - outputs/search_worldcat/records_with_errors_when_searching_worldcat.csv
        Records where an error was encountered
    - If any of the above output files already exists in the directory, then it
      is overwritten.
    """

    start_time = datetime.now()

    # Initialize parser and parse command-line args
    parser = init_argparse()
    args = parser.parse_args()

    # Convert input file into pandas DataFrame
    data = None
    if args.input_file.endswith('.csv'):
        data = pd.read_csv(args.input_file,
            dtype=str,
            keep_default_na=False)
    elif args.input_file.endswith('.xlsx'):
        data = pd.read_excel(args.input_file, 'Sheet1', engine='openpyxl',
            dtype=str,
            keep_default_na=False)
    elif args.input_file.endswith('.xls'):
        data = pd.read_excel(args.input_file, 'Sheet1', engine='xlrd',
            dtype=str,
            keep_default_na=False)
    else:
        raise ValueError(f'Invalid format for input file ({args.input_file}). '
            f'Must be one of the following file formats: CSV (.csv) or Excel '
            f'(.xlsx or .xls).')

    # Configure logging
    logging.config.fileConfig(
        'logging.conf',
        defaults={'log_filename': f'logs/search_worldcat_'
            f'{start_time.strftime("%Y-%m-%d_%H-%M-%S")}.log'},
        disable_existing_loggers=False)

    command_line_args_str = (f'command-line args:\n'
        f'input_file = {args.input_file}\n'
        f'search_my_library_holdings_first = '
        f'{args.search_my_library_holdings_first}')

    logger.info(f'Started {parser.prog} script with {command_line_args_str}')

    # Add results columns to DataFrame
    data['oclc_num'] = np.nan
    data[f"num_records_held_by_{os.environ['OCLC_INSTITUTION_SYMBOL']}"] = \
        np.nan
    data['num_records_total'] = np.nan
    data['error'] = np.nan

    records_already_processed = set()
    records_buffer = libraries.records_buffer.RecordSearchBuffer(data)

    # Loop over rows in DataFrame
    for row in data.itertuples(name='Record_from_input_file'):
        logger.debug(f'Started processing row {row.Index + 2} of input file...')
        error_occurred = False
        consecutive_errors_occurred = False
        error_msg = None

        try:
            mms_id = libraries.record.get_valid_record_identifier(
                row.mms_id,
                'MMS ID'
            )

            assert mms_id not in records_already_processed, (f'Record with MMS '
                f'ID {mms_id} has already been processed.')
            records_already_processed.add(mms_id)

            assert len(records_buffer) == 0, (f'Records buffer was not '
                f'properly emptied. It currently contains '
                f'{len(records_buffer)} record(s).')

            # Add current row's data to the empty buffer and process that record
            records_buffer.add(row)
            records_buffer.process_records(
                args.search_my_library_holdings_first
            )
        except AssertionError as assert_err:
            logger.exception(f'An assertion error occurred: {assert_err}')
            error_msg = f'Assertion Error: {assert_err}'
            error_occurred = True
        except HTTPError as first_http_err:
            logger.exception(f'An HTTP error occurred: {first_http_err}')
            error_msg = f'HTTP Error: {first_http_err}'
            error_occurred = True

            http_status_code = ''
            if hasattr(first_http_err, 'response'):
                if hasattr(first_http_err.response, 'text'):
                    logger.error(f'API Response:\n'
                        f'{first_http_err.response.text}')
                http_status_code = getattr(
                    first_http_err.response,
                    'status_code',
                    ''
                )

            if str(http_status_code).startswith('5'):
                # Try processing records buffer again
                try:
                    logger.debug('Trying one more time to process this records '
                        'buffer...')
                    records_buffer.process_records(
                        args.search_my_library_holdings_first
                    )

                    # Records buffer was processed without an HTTP error this
                    # time, so reset error variables
                    error_msg = None
                    error_occurred = False
                except HTTPError as second_http_err:
                    logger.exception(f'A second HTTP error occurred when '
                        f'reprocessing the same records buffer: '
                        f'{second_http_err}')
                    error_msg = f'HTTP Error: {second_http_err}'
                    error_occurred = True
                    consecutive_errors_occurred = True
        except JSONDecodeError as json_decode_err:
            logger.exception(f'A JSON Decode Error occurred: {json_decode_err}')
            error_msg = f'JSON Decode Error: {json_decode_err}'
            error_occurred = True
        except Exception as err:
            logger.exception(f'An error occurred: {err}')
            error_msg = f'{err}'
            error_occurred = True
        finally:
            if error_occurred:
                logger.error(f"This error occurred when processing MMS ID "
                    f"'{row.mms_id}' (at row {row.Index + 2} of input file).\n")

                # Update Error column of input file for the given row
                data.loc[row.Index, 'error'] = error_msg

            logger.debug(f'Finished processing row {row.Index + 2} of input '
                f'file.\n')

            # If a second attempt to process this records buffer fails, then
            # don't process any more records from input file
            if consecutive_errors_occurred:
                logger.error('Consecutive errors occurred when processing the '
                    'same records buffer. Halting script.\n')
                break

            # Now that row has been processed, clear buffer
            records_buffer.remove_all_records()

    logger.debug(f'Updated DataFrame:\n{data}\n')

    # Create CSV output files
    records_with_oclc_num = data.dropna(subset=['oclc_num'])
    logger.debug(f'Records with a single OCLC Number:\n{records_with_oclc_num}'
        f'\n')
    records_with_oclc_num.to_csv(
        'outputs/search_worldcat/records_with_oclc_num.csv',
        # columns=['mms_id', 'oclc_num'],
        # header=['MMS ID', 'OCLC Number'],
        index=False)

    records_with_zero_or_multiple_worldcat_matches = \
        data.dropna(how='all', subset=[
            f"num_records_held_by_{os.environ['OCLC_INSTITUTION_SYMBOL']}",
            'num_records_total'])
        # This drops the rows where BOTH num_records values are missing
    logger.debug(f'Records with zero or multiple WorldCat matches:\n'
        f'{records_with_zero_or_multiple_worldcat_matches}\n')
    records_with_zero_or_multiple_worldcat_matches.to_csv(
        'outputs/search_worldcat/records_with_zero_or_multiple_worldcat_matches.csv',
        # columns=['mms_id', 'lccn_fixed', 'lccn', 'isbn', 'issn'],
        index=False)

    records_with_errors = data.dropna(subset=['error'])
    logger.debug(f'Records with errors:\n{records_with_errors}\n')
    records_with_errors.to_csv(
        'outputs/search_worldcat/records_with_errors_when_searching_worldcat.csv',
        # columns=['mms_id', 'lccn_fixed', 'lccn', 'isbn', 'issn', 'error'],
        index=False)

    logger.info(f'Finished {parser.prog} script with {command_line_args_str}\n')

    logger.info(f'Script completed in: {datetime.now() - start_time} '
        f'(hours:minutes:seconds.microseconds).\n')

    logger.info(f'The script made {records_buffer.num_api_requests_made} total '
        f'API request(s):\n'
        f'- {records_buffer.num_records_needing_one_api_request} Alma '
        f'record(s) needed a single WorldCat API request\n'
        f'- {records_buffer.num_records_needing_two_api_requests} Alma '
        f'record(s) needed two WorldCat API requests (which totals '
        f'{records_buffer.num_records_needing_two_api_requests * 2} API '
        f'requests)\n')

    total_records_in_output_files = (
        len(records_with_oclc_num.index)
        + len(records_with_zero_or_multiple_worldcat_matches.index)
        + len(records_with_errors.index))

    logger.info(f'Processed {total_records_in_output_files} of '
        f'{len(data.index)} row(s) from input file:\n'
        f'- {len(records_with_oclc_num.index)} record(s) with OCLC Number\n'
        f'- {len(records_with_zero_or_multiple_worldcat_matches.index)} '
        f'record(s) with zero or multiple WorldCat matches\n'
        f'- {len(records_with_errors.index)} record(s) with errors\n')

    assert len(data.index) == total_records_in_output_files, (f'Total records '
        f'in input file ({len(data.index)}) do not equal total records in '
        f'output files ({total_records_in_output_files}).\n')


if __name__ == "__main__":
    main()
