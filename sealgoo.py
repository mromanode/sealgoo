from bs4 import BeautifulSoup  # type: ignore
from pathlib import Path
from urllib.parse import urlparse
from selenium import webdriver  # type: ignore
from selenium.webdriver.chrome.options import Options  # type: ignore
from selenium.webdriver.chrome.service import Service  # type: ignore
from selenium.webdriver.common.by import By  # type: ignore
from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
from selenium.webdriver.support import expected_conditions as EC  # type: ignore

import urllib.parse
import os
import time
import sqlite3
import requests  # type: ignore


FILE_TYPES = ["pdf", "xlsx", "pptx", "docx", "csv"]
DOWNLOAD_DIR = Path("downloaded_files")
DATABASE_DIR = Path("databases")


def setup_domain_database(domain):
    """
    Initialize and configure a SQLite database for a specific domain's download information.

    Creates a SQLite database file named '<domain>_downloads.db' in the databases directory
    if it doesn't exist, and sets up the necessary table structure for storing download records.

    Args:
        domain (str): Domain name for which to create the database

    Returns:
        tuple: A tuple containing:
            - sqlite3.Connection: Database connection object
            - sqlite3.Cursor: Database cursor object

    Raises:
        sqlite3.Error: If database initialization fails
    """
    DATABASE_DIR.mkdir(exist_ok=True)
    db_path = DATABASE_DIR / f"{domain}_downloads.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            file_type TEXT,
            local_filename TEXT,
            download_status TEXT,
            file_size INTEGER,
            download_duration REAL,
            http_status INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    conn.commit()
    return conn, cursor


def setup_selenium_driver(driver_path):
    """
    Configure and initialize a Selenium Chrome WebDriver instance.

    Sets up Chrome WebDriver with specified options, including download directory
    preferences and browser settings. The driver is configured for visible operation
    to allow manual CAPTCHA solving.

    Args:
        driver_path (str): Full path to the ChromeDriver executable

    Returns:
        selenium.webdriver.Chrome: Configured Chrome WebDriver instance

    Raises:
        FileNotFoundError: If ChromeDriver executable is not found at specified path
        ValueError: If specified path is not a file
        PermissionError: If ChromeDriver is not executable
        RuntimeError: If WebDriver initialization fails
    """
    driver_path = Path(driver_path)
    if not driver_path.exists():
        raise FileNotFoundError(f"ChromeDriver not found at: {driver_path}")

    if not driver_path.is_file():
        raise ValueError(f"Specified path is not a file: {driver_path}")

    if not os.access(driver_path, os.X_OK):
        raise PermissionError(f"ChromeDriver is not executable: {driver_path}")

    chrome_options = Options()
    chrome_options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(DOWNLOAD_DIR.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.disabled": True,
        },
    )

    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")

    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    service = Service(executable_path=str(driver_path))
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def handle_captcha(driver, url):
    """
    Handle Google CAPTCHA challenges using Selenium WebDriver.

    Loads the specified URL and checks for CAPTCHA presence using multiple
    indicators. If a CAPTCHA is detected, prompts the user to solve it manually
    and waits for confirmation before proceeding.

    Args:
        driver (selenium.webdriver.Chrome): Configured Chrome WebDriver instance
        url (str): URL to load and check for CAPTCHA

    Returns:
        bool: True if CAPTCHA was successfully handled or not present,
              False if CAPTCHA handling failed

    Notes:
        - Uses multiple CAPTCHA detection methods (ID, class, text content)
        - Requires manual user intervention for CAPTCHA solving
        - Includes verification of successful CAPTCHA completion
    """
    try:
        driver.get(url)

        captcha_indicators = [
            (By.ID, "recaptcha"),
            (By.CLASS_NAME, "g-recaptcha"),
            (By.XPATH, "//*[contains(text(), 'prove you are human')]"),
            (By.XPATH, "//*[contains(text(), 'not a robot')]"),
        ]

        captcha_found = False
        for by, value in captcha_indicators:
            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((by, value)))
                captcha_found = True
                break
            except Exception as e:
                print(e)
                continue

        if captcha_found or "sorry" in driver.current_url.lower():
            print("\nCAPTCHA detected!")
            print("Please solve the CAPTCHA in the browser window.")
            print("After solving the CAPTCHA, return here and press Enter to continue...")
            input("\nPress Enter after solving the CAPTCHA...")

            try:
                WebDriverWait(driver, 10).until(EC.url_contains("search"))
                print("\nCAPTCHA solved successfully!")
                return True
            except Exception as e:
                print("\nFailed to verify CAPTCHA solution. Please try again. %s", e)
                return False
        return True

    except Exception as e:
        print("Error handling CAPTCHA: %s", e)
        return False


def create_domain_directory(domain):
    """
    Create a directory for the specified domain under the main download directory.

    Args:
        domain (str): Domain name for which to create a directory

    Returns:
        Path: Path object representing the domain-specific directory

    Notes:
        - Creates directory if it doesn't exist
        - Uses domain name as directory name
        - Ensures directory is under main DOWNLOAD_DIR
    """
    domain_dir = DOWNLOAD_DIR / domain
    domain_dir.mkdir(exist_ok=True)
    return domain_dir


def download_file(url, domain, file_type, cursor, conn):
    """
    Download a file from the specified URL and record the result in the domain's database.

    Attempts to download the file using requests, saves it to the domain-specific
    directory, and logs detailed information in the domain's SQLite database.

    Args:
        url (str): URL of the file to download
        domain (str): Source domain of the file
        file_type (str): Type of file being downloaded (e.g., 'pdf', 'xlsx')
        cursor (sqlite3.Cursor): Database cursor for executing queries
        conn (sqlite3.Connection): Database connection for committing changes

    Returns:
        bool: True if download was successful, False otherwise

    Notes:
        - Uses streaming download for large files
        - Generates filename from URL or creates unique name if needed
        - Logs detailed download information including size and duration
        - Stores files in domain-specific directories
    """
    try:
        domain_dir = create_domain_directory(domain)

        parsed_url = urlparse(url)
        filename = Path(parsed_url.path).name
        if not filename:
            filename = f"{domain}_{time.time()}.{file_type}"

        local_path = domain_dir / filename

        start_time = time.time()
        response = requests.get(url, stream=True)
        download_duration = time.time() - start_time

        file_size = int(response.headers.get("content-length", 0))
        http_status = response.status_code

        if response.status_code == 200:
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            cursor.execute(
                """
                INSERT INTO downloads (url, file_type, local_filename, download_status,
                                     file_size, download_duration, http_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (url, file_type, str(local_path), "SUCCESS", file_size, download_duration, http_status),
            )
            conn.commit()
            print(f"Successfully downloaded: {filename} to {domain_dir}")
            return True
        else:
            cursor.execute(
                """
                INSERT INTO downloads (url, file_type, local_filename, download_status,
                                     file_size, download_duration, http_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (url, file_type, str(local_path), "FAILED", file_size, download_duration, http_status),
            )
            conn.commit()
            print(f"Failed to download: {url}")
            return False

    except Exception as e:
        print(f"Error downloading {url}: {e}")
        cursor.execute(
            """
            INSERT INTO downloads (url, file_type, local_filename, download_status,
                                 file_size, download_duration, http_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                url,
                file_type,
                str(local_path) if "local_path" in locals() else filename,
                f"ERROR: {str(e)}",
                0,
                time.time() - start_time if "start_time" in locals() else 0,
                response.status_code if "response" in locals() else 0,
            ),
        )
        conn.commit()
        return False


def process_domain(domain, driver_path, file_types):
    """
    Process a single domain for all specified file types.

    Performs Google searches for each file type using the site: and filetype:
    operators, handles CAPTCHAs, and downloads matching files to domain-specific
    directories. Uses domain-specific database for logging.

    Args:
        domain (str): Domain to search (e.g., 'example.com')
        driver_path (str): Path to ChromeDriver executable
        file_types (list): List of file types to search for

    Notes:
        - Implements rate limiting between searches
        - Handles CAPTCHAs for each search
        - Downloads and logs all matching files
        - Includes error handling for each file type
        - Organizes files and databases by domain
    """
    conn, cursor = setup_domain_database(domain)
    driver = setup_selenium_driver(driver_path)

    try:
        for file_type in file_types:
            print(f"\nProcessing {file_type} files for domain: {domain}")

            query = f"site:{domain} filetype:{file_type}"
            search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=10"

            if not handle_captcha(driver, search_url):
                print(f"Skipping {file_type} for {domain} due to CAPTCHA failure")
                continue

            html = driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            search_results = soup.find_all("div", class_="g")

            for result in search_results:
                link = result.find("a")
                if link and "href" in link.attrs:
                    url = link["href"]
                    if f".{file_type}" in url.lower():
                        print(f"Found {file_type} file: {url}")
                        download_file(url, domain, file_type, cursor, conn)

            time.sleep(2)

    except Exception as e:
        print(f"Error processing domain {domain}: {e}")
    finally:
        driver.quit()
        conn.close()
        print(f"\nClosed database connection for {domain}")


if __name__ == "__main__":
    """
    Main execution function for bulk domain processing.

    Handles user input for ChromeDriver path and domains, sets up necessary
    directories, and processes all domains for specified file types.
    Organizes downloaded files and creates separate databases for each domain.

    Process:
        1. Creates main download and database directories
        2. Gets ChromeDriver path
        3. Collects domains from user
        4. Processes each domain for all file types
        5. Handles CAPTCHAs and downloads files
        6. Maintains domain-specific database records

    Notes:
        - Implements comprehensive error handling
        - Ensures proper resource cleanup
        - Provides user feedback throughout process
        - Requires manual CAPTCHA solving
        - Organizes files and databases by domain
    """
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    DATABASE_DIR.mkdir(exist_ok=True)

    driver_path = input("Enter the path to ChromeDriver executable: ").strip()
    if not Path(driver_path).exists():
        print("Invalid ChromeDriver path")
        quit()

    print("\nEnter domains (one per line, press Enter twice to finish):")
    domains: list[str] = []
    while True:
        domain = input().strip()
        if domain == "":
            if domains:
                break
            continue
        domains.append(domain)

    try:
        for domain in domains:
            print(f"\nProcessing domain: {domain}")
            process_domain(domain, driver_path, FILE_TYPES)

    except Exception as e:
        print(f"Error: {e}")
