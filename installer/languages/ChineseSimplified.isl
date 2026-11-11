; *** Inno Setup 简体中文语言包 ***
;
; 官方发行版不自带中文（jrsoftware 把翻译单独放在网站上），
; 这份是按官方 Default.isl (6.5+) 的全部键逐条翻译的，键名一一对应、不缺项。
; 以后 Inno 升级新增了键，这里没有的会自动回退成英文，不会编译失败。
;
; 翻译约定：原文末尾没有句号的，译文也不要加句号 —— Inno 会自动补，
; 加了会显示成两个句号。

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
; 936 = 简体中文 GBK。文件本身是 UTF-8(BOM)，这里的 CodePage 只给非 Unicode 模式用
LanguageCodePage=936
DialogFontName=微软雅黑
DialogFontSize=9
WelcomeFontName=微软雅黑
WelcomeFontSize=12

[Messages]

; *** 窗口标题
SetupAppTitle=安装
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=%1 卸载

; *** 通用标题
InformationTitle=提示
ConfirmTitle=确认
ErrorTitle=错误

; *** 引导程序（SetupLdr）消息
SetupLdrStartupMessage=即将安装 %1。是否继续？
LdrCannotCreateTemp=无法创建临时文件，安装已中止
LdrCannotExecTemp=无法执行临时目录中的文件，安装已中止
HelpTextNote=

; *** 启动错误
LastErrorMessage=%1.%n%n错误 %2: %3
SetupFileMissing=安装目录中缺少文件 %1。请解决该问题，或重新获取一份完整的安装程序。
SetupFileCorrupt=安装文件已损坏。请重新获取一份完整的安装程序。
SetupFileCorruptOrWrongVer=安装文件已损坏，或与当前版本的安装程序不兼容。请解决该问题，或重新获取一份完整的安装程序。
InvalidParameter=命令行传入了无效参数：%n%n%1
SetupAlreadyRunning=安装程序已经在运行。
WindowsVersionNotSupported=本程序不支持你当前运行的 Windows 版本。
WindowsServicePackRequired=本程序需要 %1 Service Pack %2 或更高版本。
NotOnThisPlatform=本程序无法在 %1 上运行。
OnlyOnThisPlatform=本程序只能在 %1 上运行。
OnlyOnTheseArchitectures=本程序只能安装在面向以下处理器架构设计的 Windows 版本上：%n%n%1
WinVersionTooLowError=本程序需要 %1 %2 或更高版本。
WinVersionTooHighError=本程序无法安装在 %1 %2 或更高版本上。
AdminPrivilegesRequired=安装本程序时必须以管理员身份登录。
PowerUserPrivilegesRequired=安装本程序时必须以管理员或 Power Users 组成员的身份登录。
SetupAppRunningError=安装程序检测到 %1 正在运行。%n%n请现在关闭它的所有实例，然后单击"确定"继续，或单击"取消"退出。
UninstallAppRunningError=卸载程序检测到 %1 正在运行。%n%n请现在关闭它的所有实例，然后单击"确定"继续，或单击"取消"退出。

; *** 启动询问
PrivilegesRequiredOverrideTitle=选择安装模式
PrivilegesRequiredOverrideInstruction=请选择安装模式
PrivilegesRequiredOverrideText1=%1 可以为所有用户安装（需要管理员权限），也可以只为你安装。
PrivilegesRequiredOverrideText2=%1 可以只为你安装，也可以为所有用户安装（需要管理员权限）。
PrivilegesRequiredOverrideAllUsers=为所有用户安装(&A)
PrivilegesRequiredOverrideAllUsersRecommended=为所有用户安装(&A)（推荐）
PrivilegesRequiredOverrideCurrentUser=只为我安装(&M)
PrivilegesRequiredOverrideCurrentUserRecommended=只为我安装(&M)（推荐）

; *** 其他错误
ErrorCreatingDir=安装程序无法创建目录"%1"
ErrorTooManyFilesInDir=无法在目录"%1"中创建文件，因为该目录中的文件过多

; *** 安装程序公共消息
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。如果现在退出，程序将不会被安装。%n%n你可以在以后再次运行安装程序来完成安装。%n%n确定退出安装吗？
AboutSetupMenuItem=关于安装程序(&A)...
AboutSetupTitle=关于安装程序
AboutSetupMessage=%1 版本 %2%n%3%n%n%1 主页：%n%4
AboutSetupNote=
TranslatorNote=

; *** 按钮
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonYesToAll=全部选是(&A)
ButtonNo=否(&N)
ButtonNoToAll=全部选否(&O)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ButtonWizardBrowse=浏览(&R)...
ButtonNewFolder=新建文件夹(&M)

; *** "选择语言" 对话框
SelectLanguageTitle=选择安装语言
SelectLanguageLabel=请选择安装过程中使用的语言。

; *** 公共向导文本
ClickNext=单击"下一步"继续，或单击"取消"退出安装程序。
BeveledLabel=
BrowseDialogTitle=浏览文件夹
BrowseDialogLabel=请从下面的列表中选择一个文件夹，然后单击"确定"。
NewFolderName=新建文件夹

; *** "欢迎" 页
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=本向导将把 [name/ver] 安装到你的电脑上。%n%n建议你在继续之前关闭所有其他应用程序。

; *** "密码" 页
WizardPassword=密码
PasswordLabel1=本次安装受密码保护。
PasswordLabel3=请输入密码，然后单击"下一步"继续。密码区分大小写。
PasswordEditLabel=密码(&P)：
IncorrectPassword=你输入的密码不正确，请重新输入。

; *** "许可协议" 页
WizardLicense=许可协议
LicenseLabel=请在继续之前阅读以下重要信息。
LicenseLabel3=请阅读以下许可协议。你必须接受本协议中的条款才能继续安装。
LicenseAccepted=我接受协议(&A)
LicenseNotAccepted=我不接受协议(&D)

; *** "信息" 页
WizardInfoBefore=信息
InfoBeforeLabel=请在继续之前阅读以下重要信息。
InfoBeforeClickLabel=准备好继续安装时，单击"下一步"。
WizardInfoAfter=信息
InfoAfterLabel=请在继续之前阅读以下重要信息。
InfoAfterClickLabel=准备好继续安装时，单击"下一步"。

; *** "用户信息" 页
WizardUserInfo=用户信息
UserInfoDesc=请输入你的信息。
UserInfoName=用户名(&U)：
UserInfoOrg=组织(&O)：
UserInfoSerial=序列号(&S)：
UserInfoNameRequired=你必须输入一个名称。

; *** "选择目标位置" 页
WizardSelectDir=选择目标位置
SelectDirDesc=[name] 应安装在哪里？
SelectDirLabel3=安装程序将把 [name] 安装到下面的文件夹中。
SelectDirBrowseLabel=单击"下一步"继续。如果你想选择其他文件夹，请单击"浏览"。
DiskSpaceGBLabel=至少需要 [gb] GB 的可用磁盘空间。
DiskSpaceMBLabel=至少需要 [mb] MB 的可用磁盘空间。
CannotInstallToNetworkDrive=安装程序无法安装到网络驱动器。
CannotInstallToUNCPath=安装程序无法安装到 UNC 路径。
InvalidPath=你必须输入带盘符的完整路径，例如：%n%nC:\APP%n%n或形如下面的 UNC 路径：%n%n\\server\share
InvalidDrive=你选择的驱动器或 UNC 共享不存在或无法访问。请选择另一个。
DiskSpaceWarningTitle=磁盘空间不足
DiskSpaceWarning=安装程序至少需要 %1 KB 的可用空间，但所选驱动器仅有 %2 KB 可用。%n%n你确定要继续吗？
DirNameTooLong=文件夹名称或路径过长。
InvalidDirName=文件夹名称无效。
BadDirName32=文件夹名称不能包含下列任何字符：%n%n%1
DirExistsTitle=文件夹已存在
DirExists=文件夹：%n%n%1%n%n已存在。你确定还要安装到该文件夹吗？
DirDoesntExistTitle=文件夹不存在
DirDoesntExist=文件夹：%n%n%1%n%n不存在。你希望创建该文件夹吗？

; *** "选择组件" 页
WizardSelectComponents=选择组件
SelectComponentsDesc=应安装哪些组件？
SelectComponentsLabel2=请选中你要安装的组件，取消选中你不想安装的组件。准备好继续时单击"下一步"。
FullInstallation=完整安装
CompactInstallation=精简安装
CustomInstallation=自定义安装
NoUninstallWarningTitle=组件已存在
NoUninstallWarning=安装程序检测到下列组件已安装在你的电脑上：%n%n%1%n%n取消选中这些组件并不会卸载它们。%n%n你确定要继续吗？
ComponentSize1=%1 KB
ComponentSize2=%1 MB
ComponentsDiskSpaceGBLabel=当前选择至少需要 [gb] GB 的磁盘空间。
ComponentsDiskSpaceMBLabel=当前选择至少需要 [mb] MB 的磁盘空间。

; *** "选择附加任务" 页
WizardSelectTasks=选择附加任务
SelectTasksDesc=应执行哪些附加任务？
SelectTasksLabel2=请选择你希望安装程序在安装 [name] 时执行的附加任务，然后单击"下一步"。

; *** "选择开始菜单文件夹" 页
WizardSelectProgramGroup=选择开始菜单文件夹
SelectStartMenuFolderDesc=安装程序应把程序的快捷方式放在哪里？
SelectStartMenuFolderLabel3=安装程序将在下面的开始菜单文件夹中创建程序的快捷方式。
SelectStartMenuFolderBrowseLabel=单击"下一步"继续。如果你想选择其他文件夹，请单击"浏览"。
MustEnterGroupName=你必须输入一个文件夹名称。
GroupNameTooLong=文件夹名称或路径过长。
InvalidGroupName=文件夹名称无效。
BadGroupName=文件夹名称不能包含下列任何字符：%n%n%1
NoProgramGroupCheck2=不创建开始菜单文件夹(&D)

; *** "准备安装" 页
WizardReady=准备安装
ReadyLabel1=安装程序已准备好开始在你的电脑上安装 [name]。
ReadyLabel2a=单击"安装"继续安装，或单击"上一步"查看或修改任何设置。
ReadyLabel2b=单击"安装"继续安装。
ReadyMemoUserInfo=用户信息：
ReadyMemoDir=目标位置：
ReadyMemoType=安装类型：
ReadyMemoComponents=选定组件：
ReadyMemoGroup=开始菜单文件夹：
ReadyMemoTasks=附加任务：

; *** 下载页
DownloadingLabel2=正在下载文件...
ButtonStopDownload=停止下载(&S)
StopDownload=你确定要停止下载吗？
ErrorDownloadAborted=下载已中止
ErrorDownloadFailed=下载失败：%1 %2
ErrorDownloadSizeFailed=获取大小失败：%1 %2
ErrorProgress=进度无效：%1 / %2
ErrorFileSize=文件大小无效：应为 %1，实际为 %2

; *** 解压页
ExtractingLabel=正在解压文件...
ButtonStopExtraction=停止解压(&S)
StopExtraction=你确定要停止解压吗？
ErrorExtractionAborted=解压已中止
ErrorExtractionFailed=解压失败：%1

; *** 解压失败详情
ArchiveIncorrectPassword=密码不正确
ArchiveIsCorrupted=压缩包已损坏
ArchiveUnsupportedFormat=不支持该压缩格式

; *** "正在准备安装" 页
WizardPreparing=正在准备安装
PreparingDesc=安装程序正在准备在你的电脑上安装 [name]。
PreviousInstallNotCompleted=上一次程序的安装/卸载尚未完成。你需要重启电脑来完成那次安装。%n%n重启电脑后，请再次运行安装程序以完成 [name] 的安装。
CannotContinue=安装程序无法继续。请单击"取消"退出。
ApplicationsFound=下列应用程序正在使用需要被安装程序更新的文件。建议允许安装程序自动关闭这些应用程序。
ApplicationsFound2=下列应用程序正在使用需要被安装程序更新的文件。建议允许安装程序自动关闭这些应用程序。安装完成后，安装程序会尝试重新打开它们。
CloseApplications=自动关闭这些应用程序(&A)
DontCloseApplications=不要关闭这些应用程序(&D)
ErrorCloseApplications=安装程序无法自动关闭所有应用程序。建议在继续之前手动关闭所有正在使用待更新文件的应用程序。
PrepareToInstallNeedsRestart=安装程序必须重启你的电脑。重启后，请再次运行安装程序以完成 [name] 的安装。%n%n你希望现在重启吗？

; *** "正在安装" 页
WizardInstalling=正在安装
InstallingLabel=安装程序正在把 [name] 安装到你的电脑上，请稍候。

; *** "安装完成" 页
FinishedHeadingLabel=正在完成 [name] 安装向导
FinishedLabelNoIcons=安装程序已成功在你的电脑上安装 [name]。
FinishedLabel=安装程序已成功在你的电脑上安装 [name]。可以通过选择已安装的快捷方式启动应用程序。
ClickFinish=单击"完成"退出安装程序。
FinishedRestartLabel=为完成 [name] 的安装，安装程序必须重启你的电脑。你希望现在重启吗？
FinishedRestartMessage=为完成 [name] 的安装，安装程序必须重启你的电脑。%n%n你希望现在重启吗？
ShowReadmeCheck=是的，我想查看自述文件
YesRadio=是的，立即重启电脑(&Y)
NoRadio=不，我稍后自己重启(&N)
; 例如用作"运行 MyProg.exe"
RunEntryExec=运行 %1
; 例如用作"查看 Readme.txt"
RunEntryShellExec=查看 %1

; *** "需要下一张盘"
ChangeDiskTitle=安装程序需要下一张盘
SelectDiskLabel2=请插入 %1 号盘，然后单击"确定"。%n%n如果这张盘上的文件不在下面显示的文件夹中，请输入正确的路径或单击"浏览"。
PathLabel=路径(&P)：
FileNotInDir2=在"%2"中找不到文件"%1"。请插入正确的磁盘或选择其他文件夹。
SelectDirectoryLabel=请指定下一张盘的位置。

; *** 安装阶段消息
SetupAborted=安装未完成。%n%n请解决问题后再次运行安装程序。
AbortRetryIgnoreSelectAction=选择操作
AbortRetryIgnoreRetry=重试(&T)
AbortRetryIgnoreIgnore=忽略错误并继续(&I)
AbortRetryIgnoreCancel=取消安装
RetryCancelSelectAction=选择操作
RetryCancelRetry=重试(&T)
RetryCancelCancel=取消

; *** 安装状态消息
StatusClosingApplications=正在关闭应用程序...
StatusCreateDirs=正在创建目录...
StatusExtractFiles=正在解压文件...
StatusDownloadFiles=正在下载文件...
StatusCreateIcons=正在创建快捷方式...
StatusCreateIniEntries=正在创建 INI 条目...
StatusCreateRegistryEntries=正在创建注册表条目...
StatusRegisterFiles=正在注册文件...
StatusSavingUninstall=正在保存卸载信息...
StatusRunProgram=正在完成安装...
StatusRestartingApplications=正在重新启动应用程序...
StatusRollback=正在回滚更改...

; *** 其他错误
ErrorInternal2=内部错误：%1
ErrorFunctionFailedNoCode=%1 失败
ErrorFunctionFailed=%1 失败；代码 %2
ErrorFunctionFailedWithMessage=%1 失败；代码 %2。%n%3
ErrorExecutingProgram=无法执行文件：%n%1

; *** 注册表错误
ErrorRegOpenKey=打开注册表键时出错：%n%1\%2
ErrorRegCreateKey=创建注册表键时出错：%n%1\%2
ErrorRegWriteKey=写入注册表键时出错：%n%1\%2

; *** INI 错误
ErrorIniEntry=在文件"%1"中创建 INI 条目时出错。

; *** 文件复制错误
FileAbortRetryIgnoreSkipNotRecommended=跳过此文件（不推荐）(&S)
FileAbortRetryIgnoreIgnoreNotRecommended=忽略错误并继续（不推荐）(&I)
SourceIsCorrupted=源文件已损坏
SourceDoesntExist=源文件"%1"不存在
SourceVerificationFailed=源文件校验失败：%1
VerificationSignatureDoesntExist=签名文件"%1"不存在
VerificationSignatureInvalid=签名文件"%1"无效
VerificationKeyNotFound=签名文件"%1"使用了未知的密钥
VerificationFileNameIncorrect=文件名不正确
VerificationFileTagIncorrect=文件标记不正确
VerificationFileSizeIncorrect=文件大小不正确
VerificationFileHashIncorrect=文件哈希不正确
ExistingFileReadOnly2=无法替换现有文件，因为它被标记为只读。
ExistingFileReadOnlyRetry=去掉只读属性并重试(&R)
ExistingFileReadOnlyKeepExisting=保留现有文件(&K)
ErrorReadingExistingDest=尝试读取现有文件时出错：
FileExistsSelectAction=选择操作
FileExists2=该文件已存在。
FileExistsOverwriteExisting=覆盖现有文件(&O)
FileExistsKeepExisting=保留现有文件(&K)
FileExistsOverwriteOrKeepAll=对后续冲突执行相同操作(&D)
ExistingFileNewerSelectAction=选择操作
ExistingFileNewer2=现有文件比安装程序要安装的文件更新。
ExistingFileNewerOverwriteExisting=覆盖现有文件(&O)
ExistingFileNewerKeepExisting=保留现有文件（推荐）(&K)
ExistingFileNewerOverwriteOrKeepAll=对后续冲突执行相同操作(&D)
ErrorChangingAttr=尝试修改现有文件的属性时出错：
ErrorCreatingTemp=尝试在目标目录中创建文件时出错：
ErrorReadingSource=尝试读取源文件时出错：
ErrorCopying=尝试复制文件时出错：
ErrorDownloading=尝试下载文件时出错：
ErrorExtracting=尝试解压压缩包时出错：
ErrorReplacingExistingFile=尝试替换现有文件时出错：
ErrorRestartReplace=RestartReplace 失败：
ErrorRenamingTemp=尝试重命名目标目录中的文件时出错：
ErrorRegisterServer=无法注册 DLL/OCX：%1
ErrorRegSvr32Failed=RegSvr32 失败，退出码 %1
ErrorRegisterTypeLib=无法注册类型库：%1

; *** 卸载显示名称标记
; 例如用作"我的程序 (32 位)"
UninstallDisplayNameMark=%1（%2）
; 例如用作"我的程序 (32 位，所有用户)"
UninstallDisplayNameMarks=%1（%2，%3）
UninstallDisplayNameMark32Bit=32 位
UninstallDisplayNameMark64Bit=64 位
UninstallDisplayNameMarkAllUsers=所有用户
UninstallDisplayNameMarkCurrentUser=当前用户

; *** 安装后错误
ErrorOpeningReadme=尝试打开自述文件时出错。
ErrorRestartingComputer=安装程序无法重启电脑，请手动重启。

; *** 卸载程序消息
UninstallNotFound=文件"%1"不存在，无法卸载。
UninstallOpenError=文件"%1"无法打开，无法卸载。
UninstallUnsupportedVer=卸载日志文件"%1"的格式无法被当前版本的卸载程序识别，无法卸载。
UninstallUnknownEntry=卸载日志中遇到未知条目（%1）。
ConfirmUninstall=你确定要完全删除 %1 及其所有组件吗？
UninstallOnlyOnWin64=此安装只能在 64 位 Windows 上卸载。
OnlyAdminCanUninstall=此安装只能由具有管理员权限的用户卸载。
UninstallStatusLabel=正在从你的电脑中删除 %1，请稍候。
UninstalledAll=%1 已成功从你的电脑中删除。
UninstalledMost=%1 卸载完成。%n%n部分内容无法删除，你可以手动删除它们。
UninstalledAndNeedsRestart=为完成 %1 的卸载，必须重启你的电脑。%n%n你希望现在重启吗？
UninstallDataCorrupted="%1"文件已损坏，无法卸载。

; *** 卸载阶段消息
ConfirmDeleteSharedFileTitle=删除共享文件？
ConfirmDeleteSharedFile2=系统显示下列共享文件已不再被任何程序使用。你希望卸载程序删除此共享文件吗？%n%n如果仍有程序在使用该文件而将其删除，那些程序可能无法正常运行。如果不确定，请选择"否"。保留该文件不会对你的系统造成任何损害。
SharedFileNameLabel=文件名：
SharedFileLocationLabel=位置：
WizardUninstalling=卸载状态
StatusUninstalling=正在卸载 %1...

; *** 关机阻止原因
ShutdownBlockReasonInstallingApp=正在安装 %1。
ShutdownBlockReasonUninstallingApp=正在卸载 %1。

; 下面的自定义消息安装程序本身不使用，但脚本里若用到就需要翻译。

[CustomMessages]

NameAndVersion=%1 版本 %2
AdditionalIcons=附加快捷方式：
CreateDesktopIcon=创建桌面快捷方式(&D)
CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
ProgramOnTheWeb=%1 官方主页
UninstallProgram=卸载 %1
LaunchProgram=启动 %1
AssocFileExtension=将 %1 与 %2 文件扩展名关联(&A)
AssocingFileExtension=正在将 %1 与 %2 文件扩展名关联...
AutoStartProgramGroupDescription=启动：
AutoStartProgram=自动启动 %1
AddonHostProgramNotFound=在你选择的文件夹中找不到 %1。%n%n你确定要继续吗？
