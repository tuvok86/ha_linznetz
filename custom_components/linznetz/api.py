"""API client for LinzNetz Serviceportal."""
import csv
import io
import logging
import re
from datetime import datetime, timedelta

import aiohttp

_LOGGER = logging.getLogger(__name__)

SSO_BASE_URL = "https://sso.linznetz.at/realms/netzsso"
SSO_AUTH_URL = f"{SSO_BASE_URL}/protocol/openid-connect/auth"
VDI_BASE_URL = "https://services.linznetz.at/verbrauchsdateninformation"
VDI_CONSUMPTION_URL = f"{VDI_BASE_URL}/consumption.jsf"
CLIENT_ID = "verbrauchsdateninformation"


class LinzNetzAuthError(Exception):
    """Exception for authentication errors."""


class LinzNetzConnectionError(Exception):
    """Exception for connection errors."""


class LinzNetzApiClient:
    """API client for LinzNetz Serviceportal (Keycloak OIDC + JSF app)."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(),
            )
        return self._session

    async def close(self) -> None:
        """Close the session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _authenticate(self) -> None:
        """Authenticate via Keycloak OIDC login form.

        Flow:
        1. GET the SSO auth URL -> Keycloak login page
        2. Extract the login form action URL
        3. POST username/password to the form action URL
        4. Follow redirects back to VDI app (session cookies are set)
        """
        session = await self._get_session()

        try:
            # Step 1: Request the authorization endpoint to get the login page
            async with session.get(
                SSO_AUTH_URL,
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": VDI_CONSUMPTION_URL,
                    "scope": "openid",
                },
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    raise LinzNetzAuthError(
                        f"Failed to load login page (status {resp.status})"
                    )
                login_page_html = await resp.text()

            # Step 2: Extract the form action URL from the Keycloak login page
            action_match = re.search(
                r'<form\s[^>]*id="kc-form-login"[^>]*action="([^"]+)"',
                login_page_html,
            )
            if not action_match:
                # Try alternative pattern
                action_match = re.search(
                    r'<form[^>]*action="(https://sso\.linznetz\.at[^"]*)"',
                    login_page_html,
                )
            if not action_match:
                raise LinzNetzAuthError(
                    "Could not find login form action URL on Keycloak page"
                )

            login_action_url = action_match.group(1).replace("&amp;", "&")
            _LOGGER.debug("Login action URL: %s", login_action_url)

            # Step 3: POST credentials to the login form
            async with session.post(
                login_action_url,
                data={
                    "username": self._username,
                    "password": self._password,
                },
                allow_redirects=True,
            ) as resp:
                final_url = str(resp.url)
                response_text = await resp.text()

                # Check if we landed back on the login page (wrong credentials)
                if "kc-form-login" in response_text or resp.status == 401:
                    raise LinzNetzAuthError("Invalid username or password")

                # Check for error messages in the response
                error_match = re.search(
                    r'<span\s+id="input-error"[^>]*>([^<]+)</span>',
                    response_text,
                )
                if error_match:
                    raise LinzNetzAuthError(
                        f"Login failed: {error_match.group(1).strip()}"
                    )

                # Check if we successfully reached the VDI app
                if "services.linznetz.at" not in final_url:
                    # Check if still on SSO page
                    if "sso.linznetz.at" in final_url:
                        raise LinzNetzAuthError(
                            "Authentication failed - still on login page"
                        )

                _LOGGER.debug("Successfully authenticated, final URL: %s", final_url)

        except aiohttp.ClientError as err:
            raise LinzNetzConnectionError(
                f"Connection error during authentication: {err}"
            ) from err

    async def validate_credentials(self) -> bool:
        """Validate credentials by attempting to log in.

        Returns True if successful, raises LinzNetzAuthError on failure.
        """
        await self._authenticate()
        return True

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid authenticated session."""
        session = await self._get_session()

        # Quick check: try to access the VDI page
        try:
            async with session.get(
                VDI_CONSUMPTION_URL,
                allow_redirects=False,
            ) as resp:
                # If we get a redirect to SSO, we need to re-authenticate
                if resp.status in (301, 302, 303, 307):
                    location = resp.headers.get("Location", "")
                    if "sso.linznetz.at" in location:
                        _LOGGER.debug("Session expired, re-authenticating...")
                        await self._authenticate()
                        return
                # If we get 200, session is still valid
                if resp.status == 200:
                    return
        except aiohttp.ClientError:
            pass

        # Default: try to authenticate
        await self._authenticate()

    async def get_meter_points(self) -> list[dict]:
        """Get available meter points (Zählpunkte) from the VDI page.

        Returns a list of dicts with meter point information.
        """
        await self._ensure_authenticated()
        session = await self._get_session()

        try:
            async with session.get(VDI_CONSUMPTION_URL) as resp:
                if resp.status != 200:
                    raise LinzNetzConnectionError(
                        f"Failed to load consumption page (status {resp.status})"
                    )
                html = await resp.text()

            # Extract meter point numbers from the page
            # The JSF page typically has a dropdown/select with meter points
            meter_points = []
            # Look for select options with meter point numbers (AT + 30 digits)
            matches = re.findall(
                r'(AT\d{30,31})',
                html,
            )
            for match in set(matches):
                meter_points.append({"meter_point_number": match})

            return meter_points

        except aiohttp.ClientError as err:
            raise LinzNetzConnectionError(
                f"Connection error fetching meter points: {err}"
            ) from err

    async def get_consumption_data(
        self,
        meter_point_number: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Fetch consumption data as CSV for the given meter point and date range.

        This navigates the JSF app to request and download the CSV data.
        Returns parsed CSV data as a list of dicts.
        """
        await self._ensure_authenticated()
        session = await self._get_session()

        try:
            # Step 1: Load the consumption page to get the JSF ViewState
            async with session.get(VDI_CONSUMPTION_URL) as resp:
                if resp.status != 200:
                    raise LinzNetzConnectionError(
                        f"Failed to load consumption page (status {resp.status})"
                    )
                html = await resp.text()

            # Extract the javax.faces.ViewState
            viewstate_match = re.search(
                r'name="javax\.faces\.ViewState"\s+value="([^"]+)"',
                html,
            )
            if not viewstate_match:
                # Try alternative: hidden input with id
                viewstate_match = re.search(
                    r'id="javax\.faces\.ViewState[^"]*"\s+value="([^"]+)"',
                    html,
                )
                if not viewstate_match:
                    # Try alternative: j_idt pattern
                    viewstate_match = re.search(
                        r'name="(javax\.faces\.ViewState)"\s[^>]*value="([^"]+)"',
                        html,
                    )

            if not viewstate_match:
                raise LinzNetzConnectionError(
                    "Could not find JSF ViewState token on the consumption page"
                )

            viewstate = viewstate_match.group(
                viewstate_match.lastindex
            )

            # Extract form ID and relevant component IDs from the HTML
            # The JSF form typically has an ID like "consumptionForm" or similar
            form_id = self._extract_form_id(html)

            # Format dates for the form
            date_from_str = date_from.strftime("%d.%m.%Y")
            date_to_str = date_to.strftime("%d.%m.%Y")

            _LOGGER.debug(
                "Requesting consumption data for %s from %s to %s",
                meter_point_number,
                date_from_str,
                date_to_str,
            )

            # Step 2: Submit the form to request CSV export
            # The exact form field names depend on the JSF page structure
            # We try common patterns used in JSF applications
            form_data = {
                "javax.faces.ViewState": viewstate,
                "javax.faces.partial.ajax": "true",
            }

            # Try to find the download/export button and date fields
            csv_data = await self._try_csv_download(
                session, html, viewstate, form_id,
                meter_point_number, date_from_str, date_to_str,
            )

            return csv_data

        except aiohttp.ClientError as err:
            raise LinzNetzConnectionError(
                f"Connection error fetching consumption data: {err}"
            ) from err

    def _extract_form_id(self, html: str) -> str:
        """Extract the main form ID from the JSF page."""
        # Look for common form patterns in LinzNetz VDI
        form_match = re.search(r'<form[^>]+id="([^"]+)"[^>]*>', html)
        if form_match:
            return form_match.group(1)
        return "form"

    async def _try_csv_download(
        self,
        session: aiohttp.ClientSession,
        html: str,
        viewstate: str,
        form_id: str,
        meter_point_number: str,
        date_from_str: str,
        date_to_str: str,
    ) -> list[dict]:
        """Try to download CSV data from the JSF app.

        The LinzNetz VDI app uses JSF forms. This method attempts to find
        the correct form fields and submit a CSV export request.
        """
        # Look for a CSV/download link or button in the HTML
        # Common patterns: a]commandButton with "csv" or "export" or "download"
        download_match = re.search(
            r'id="([^"]*(?:csv|export|download|herunterladen)[^"]*)"',
            html,
            re.IGNORECASE,
        )

        # Also try to find date input fields
        date_from_field = self._find_input_field(html, ["von", "from", "dateFrom", "startDate", "start"])
        date_to_field = self._find_input_field(html, ["bis", "to", "dateTo", "endDate", "end"])
        meter_field = self._find_select_field(html, ["zaehler", "meter", "zaehlpunkt", "anlage"])

        _LOGGER.debug(
            "Found fields - date_from: %s, date_to: %s, meter: %s, download: %s",
            date_from_field,
            date_to_field,
            meter_field,
            download_match.group(1) if download_match else None,
        )

        # Build form data based on discovered fields
        form_data = {
            "javax.faces.ViewState": viewstate,
            f"{form_id}_SUBMIT": "1",
        }

        if date_from_field:
            form_data[date_from_field] = date_from_str
        if date_to_field:
            form_data[date_to_field] = date_to_str
        if meter_field:
            form_data[meter_field] = meter_point_number

        # If we found a download button, add it as the source
        if download_match:
            button_id = download_match.group(1)
            form_data["javax.faces.source"] = button_id
            form_data[button_id] = button_id

        # Try the CSV download endpoint directly
        # Many JSF apps have a separate servlet/resource for CSV export
        csv_url_match = re.search(
            r'href="([^"]*(?:csv|export|download)[^"]*)"',
            html,
            re.IGNORECASE,
        )

        if csv_url_match:
            csv_url = csv_url_match.group(1)
            if not csv_url.startswith("http"):
                csv_url = f"{VDI_BASE_URL}/{csv_url}"

            async with session.get(csv_url) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get("Content-Type", "")
                    if "csv" in content_type or "text" in content_type:
                        csv_text = await resp.text()
                        return self._parse_csv_text(csv_text)

        # Try posting the form to get CSV data
        async with session.post(
            VDI_CONSUMPTION_URL,
            data=form_data,
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "csv" in content_type or "octet-stream" in content_type:
                csv_text = await resp.text()
                return self._parse_csv_text(csv_text)

            # If we got HTML back, the form structure may be different
            # Log what we got for debugging
            response_html = await resp.text()
            _LOGGER.debug(
                "CSV download returned HTML (status %d, type: %s). "
                "Form field discovery may need adjustment.",
                resp.status,
                content_type,
            )

            # Try to find a direct download link in the response
            csv_url_match = re.search(
                r'href="([^"]*(?:\.csv|export|download)[^"]*)"',
                response_html,
                re.IGNORECASE,
            )
            if csv_url_match:
                csv_url = csv_url_match.group(1)
                if not csv_url.startswith("http"):
                    csv_url = f"{VDI_BASE_URL}/{csv_url}"
                async with session.get(csv_url) as csv_resp:
                    if csv_resp.status == 200:
                        csv_text = await csv_resp.text()
                        return self._parse_csv_text(csv_text)

        raise LinzNetzConnectionError(
            "Could not download CSV data from LinzNetz portal. "
            "The portal structure may have changed. "
            "You can still use the manual CSV import as a fallback."
        )

    def _find_input_field(self, html: str, keywords: list[str]) -> str | None:
        """Find an input field name by keywords in the id or name."""
        for keyword in keywords:
            match = re.search(
                rf'<input[^>]*(?:id|name)="([^"]*{keyword}[^"]*)"',
                html,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None

    def _find_select_field(self, html: str, keywords: list[str]) -> str | None:
        """Find a select field name by keywords in the id or name."""
        for keyword in keywords:
            match = re.search(
                rf'<select[^>]*(?:id|name)="([^"]*{keyword}[^"]*)"',
                html,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _parse_csv_text(csv_text: str) -> list[dict]:
        """Parse CSV text into a list of dicts (same format as file-based CSV)."""
        reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")
        return list(reader)
