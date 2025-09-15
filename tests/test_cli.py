"""Tests for CLI functionality.""""""

import pytestTests for CLI module.

from unittest.mock import patch, MagicMock"""

from click.testing import CliRunner

import pytest

from smart_cal.cli import clifrom unittest.mock import patch



from smart_cal import __version__

class TestCLI:from smart_cal.cli import calculate_tax, main

    """Test cases for CLI commands."""

    

    def test_cli_help(self):class TestCalculateTax:

        """Test CLI help command."""    """Test cases for tax calculation function."""

        runner = CliRunner()

        result = runner.invoke(cli, ['--help'])    def test_calculate_tax_low_income(self):

        assert result.exit_code == 0        """Test tax calculation for low income."""

        assert 'Smart Cal' in result.output        result = calculate_tax(30000, 2024)

            assert result["income"] == 30000

    @patch('smart_cal.cli.TaxVerificationService')        assert result["year"] == 2024

    @patch('smart_cal.cli.OrderRepository')        assert result["tax_rate"] == 0.15

    def test_verify_order_tax_staging(self, mock_repo, mock_service, mock_env):        assert result["tax_amount"] == 4500

        """Test verify-order-tax command with staging environment."""        assert result["net_income"] == 25500

        # Setup mocks

        mock_service_instance = MagicMock()    def test_calculate_tax_medium_income(self):

        mock_service.return_value = mock_service_instance        """Test tax calculation for medium income."""

        mock_service_instance.verify_tax_calculation.return_value = {        result = calculate_tax(75000, 2024)

            'is_valid': True,        assert result["income"] == 75000

            'message': 'Tax calculation is correct'        assert result["year"] == 2024

        }        assert result["tax_rate"] == 0.25

                assert result["tax_amount"] == 18750

        runner = CliRunner()        assert result["net_income"] == 56250

        result = runner.invoke(cli, [

            'verify-order-tax',     def test_calculate_tax_high_income(self):

            'test_order_123',        """Test tax calculation for high income."""

            '--env', 'staging'        result = calculate_tax(150000, 2024)

        ])        assert result["income"] == 150000

                assert result["year"] == 2024

        assert result.exit_code == 0        assert result["tax_rate"] == 0.35

        assert 'Tax calculation is correct' in result.output        assert result["tax_amount"] == 52500

            assert result["net_income"] == 97500

    @patch('smart_cal.cli.TaxVerificationService')

    @patch('smart_cal.cli.OrderRepository')    def test_calculate_tax_different_year(self):

    def test_verify_order_tax_production(self, mock_repo, mock_service, mock_env):        """Test tax calculation with different year."""

        """Test verify-order-tax command with production environment."""        result = calculate_tax(50000, 2023)

        # Setup mocks        assert result["year"] == 2023

        mock_service_instance = MagicMock()

        mock_service.return_value = mock_service_instance

        mock_service_instance.verify_tax_calculation.return_value = {class TestCLI:

            'is_valid': False,    """Test cases for CLI main function."""

            'message': 'Tax calculation error found'

        }    @patch("sys.argv", ["smart-cal", "--help"])

            def test_cli_help(self, capsys):

        runner = CliRunner()        """Test CLI help command."""

        result = runner.invoke(cli, [        with pytest.raises(SystemExit) as exc_info:

            'verify-order-tax',             main()

            'test_order_123',        assert exc_info.value.code == 0

            '--env', 'production'        captured = capsys.readouterr()

        ])        assert "Smart Cal - Tax calculation and utilities" in captured.out

        

        assert result.exit_code == 0    @patch("sys.argv", ["smart-cal", "--version"])

        assert 'Tax calculation error found' in result.output    def test_cli_version(self, capsys):

            """Test CLI version command."""

    def test_verify_order_tax_invalid_env(self, mock_env):        with pytest.raises(SystemExit) as exc_info:

        """Test verify-order-tax command with invalid environment."""            main()

        runner = CliRunner()        assert exc_info.value.code == 0

        result = runner.invoke(cli, [        captured = capsys.readouterr()

            'verify-order-tax',         assert f"Smart Cal {__version__}" in captured.out

            'test_order_123',

            '--env', 'invalid'    @patch("sys.argv", ["smart-cal", "calculate-tax", "--income", "50000"])

        ])    def test_cli_calculate_tax(self, capsys):

                """Test CLI tax calculation command."""

        assert result.exit_code != 0        result = main()

        assert 'Invalid environment' in result.output        assert result == 0

            captured = capsys.readouterr()

    @patch('smart_cal.cli.TaxVerificationService')        assert "Tax Calculation Results:" in captured.out

    @patch('smart_cal.cli.OrderRepository')        assert "Income: $50,000.00" in captured.out

    def test_verify_order_tax_order_not_found(self, mock_repo, mock_service, mock_env):        assert "Tax Rate: 15.0%" in captured.out

        """Test verify-order-tax command when order is not found."""

        # Setup mocks    @patch("sys.argv", ["smart-cal", "calculate-tax", "--income", "50000", "--year", "2023"])

        mock_service_instance = MagicMock()    def test_cli_calculate_tax_with_year(self, capsys):

        mock_service.return_value = mock_service_instance        """Test CLI tax calculation command with year."""

        mock_service_instance.verify_tax_calculation.side_effect = Exception("Order not found")        result = main()

                assert result == 0

        runner = CliRunner()        captured = capsys.readouterr()

        result = runner.invoke(cli, [        assert "Year: 2023" in captured.out

            'verify-order-tax', 

            'nonexistent_order',    @patch("sys.argv", ["smart-cal"])

            '--env', 'staging'    def test_cli_no_command(self, capsys):

        ])        """Test CLI with no command."""

                result = main()

        assert result.exit_code == 0        assert result == 1

        assert 'Error' in result.output        captured = capsys.readouterr()
        assert "Available commands" in captured.out

    @patch("sys.argv", ["smart-cal", "calculate-tax", "--income", "50000", "--verbose"])
    def test_cli_verbose(self, capsys):
        """Test CLI with verbose flag."""
        result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "Tax Calculation Results:" in captured.out

