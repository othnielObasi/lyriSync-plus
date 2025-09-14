; installer_nsis.nsi — NSIS script for LyriSync+
!define APPNAME "LyriSync+"
!define APPVER  "0.2.0"
!define EXE_NAME "LyriSyncPlus.exe"

OutFile "LyriSyncPlus-Setup.exe"
InstallDir "$PROGRAMFILES\LyriSyncPlus"
RequestExecutionLevel admin

Page directory
Page instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /oname=$(EXE_NAME) "dist\main.exe"
  File "README.md"
  File "lyrisync_config.yaml"
  File "requirements.txt"
  File "iconLyriSync.ico"
  CreateDirectory "$SMPROGRAMS\LyriSync+"
  CreateShortCut "$SMPROGRAMS\LyriSync+\${APPNAME}.lnk" "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\iconLyriSync.ico"
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\iconLyriSync.ico"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\LyriSync+\${APPNAME}.lnk"
  RMDir  "$SMPROGRAMS\LyriSync+"
  Delete "$INSTDIR\${EXE_NAME}"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\lyrisync_config.yaml"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\iconLyriSync.ico"
  RMDir "$INSTDIR"
SectionEnd
