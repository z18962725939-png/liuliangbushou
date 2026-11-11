@echo off
chcp 936 >nul
REM ============================================================
REM  �������� ���� һ�������exe + Windows ��װ����
REM
REM  �� 1 ����PyInstaller �� Python ������ dist\��������\
REM  �� 2 ����Inno Setup ����ѹ��һ����װ�� installer_output\��������_Setup_vx.x.x.exe
REM
REM  �����
REM    installer_output\��������_Setup_v1.0.0.exe   <- �����ͻ��ľ������
REM    installer_output\version.json                <- �ŵ��������ϣ����û��Զ��յ�����
REM ============================================================
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ============================================================
echo   �������� - һ�����
echo ============================================================
echo.

REM -- �� 1 ������� Python --
echo [1/5] ��� Python ����...
python --version >nul 2>&1
if errorlevel 1 (
    echo ������û�ҵ� Python�����Ȱ�װ Python 3.10 ���ϰ汾��
    pause
    exit /b 1
)
python --version
echo.

REM -- �� 2 ����װ���� --
echo [2/5] ��װ/��������...
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 (
    echo ������������װʧ�ܡ�
    pause
    exit /b 1
)
echo OK
echo.

REM -- �� 3 ����ȷ�ϴ����ܵ��� --
echo [3/5] �������ܷ���������...
python -c "from justoneapi_collector.gui import run_gui; print('OK')"
if errorlevel 1 (
    echo ���������뵼��ʧ�ܣ����޺��ٴ����
    pause
    exit /b 1
)
echo.

REM -- �� 4 ������ exe --
echo [4/5] ���ڴ�� exe����һ�λ�Ƚ��������Ժ�...
python -m PyInstaller justoneapi_collector.spec --noconfirm --log-level WARN
if errorlevel 1 (
    echo ������exe ���ʧ�ܡ�
    pause
    exit /b 1
)
if not exist "dist\��������\��������.exe" (
    echo ������û�ҵ� dist\��������\��������.exe
    pause
    exit /b 1
)
echo OK��dist\��������\��������.exe
echo.

REM -- �� 5 ��������װ�� --
echo [5/5] �������ɰ�װ��...
python tools\build_setup.py
if errorlevel 1 (
    echo.
    echo ��ʾ���������˵ûװ Inno Setup��
    echo      ����������װ�����ù���ԱȨ�ޣ���scoop install extras/inno-setup
    echo      ûװ scoop ��ȥ https://jrsoftware.org ���ذ�װ��
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   �����ɣ�
echo ============================================================
echo.
echo �����ͻ����ļ���
echo   installer_output\��������_Setup_v1.0.0.exe
echo.
echo �����Ͽͻ��Զ�������
echo   �� installer_output\version.json ��� url �ĳɰ�װ����ʵ���ص�ַ��
echo   Ȼ��Ͱ�װ��һ�𴫵���ķ�������
echo.
pause
