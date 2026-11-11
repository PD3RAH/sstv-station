; SSTV Station - small update (program files only, keeps the bundled Python)
; Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
Unicode true
!include "MUI2.nsh"

Name "SSTV Station ${VERSION} update"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\SSTVStation"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SSTVStation" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "${STAGE}\sstvstation.ico"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Dutch"

; Stil (/S) gestart vanuit SSTV Station zelf: even wachten tot het programma is afgesloten,
; na afloop het programma opnieuw starten.
Function .onInit
  IfSilent 0 +2
    Sleep 2500
FunctionEnd

Function .onInstSuccess
  IfSilent 0 +2
    Call StartApp
FunctionEnd

Function .onVerifyInstDir
  IfFileExists "$INSTDIR\python\pythonw.exe" +2
    Abort
FunctionEnd

Section "Update"
  SetOutPath "$INSTDIR"
  File "${STAGE}\sstv_station.py"
  File "${STAGE}\sstv.py"
  File "${STAGE}\start.pyw"
  File "${STAGE}\README.md"
  File "${STAGE}\LICENSE"
  File "${STAGE}\DISCLAIMER.txt"
  File "${STAGE}\THIRD_PARTY_NOTICES.md"
  SetOutPath "$INSTDIR\docs"
  File "${STAGE}\docs\*.*"
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\__pycache__"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SSTVStation" "DisplayVersion" "${VERSION}"
SectionEnd

Function StartApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\start.pyw"'
FunctionEnd
