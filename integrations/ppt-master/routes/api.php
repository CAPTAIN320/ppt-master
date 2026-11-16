<?php

use Illuminate\Support\Facades\Route;
use Integrations\PptMaster\Http\Controllers\PptMasterController;

Route::get('/config', [PptMasterController::class, 'config']);

Route::get('/projects', [PptMasterController::class, 'projects']);
Route::get('/projects/{id}', [PptMasterController::class, 'project']);
Route::post('/projects/save', [PptMasterController::class, 'saveProject']);
Route::post('/projects/delete', [PptMasterController::class, 'deleteProject']);

Route::post('/strategist/plan', [PptMasterController::class, 'plan']);
Route::post('/strategist/confirm', [PptMasterController::class, 'confirm']);
Route::post('/executor/slide', [PptMasterController::class, 'slide']);
Route::post('/svg/finalize', [PptMasterController::class, 'finalizeSvg']);

Route::get('/remote/status', [PptMasterController::class, 'remoteStatus']);
Route::get('/remote/jobs', [PptMasterController::class, 'remoteJobs']);
Route::get('/remote/jobs/{id}', [PptMasterController::class, 'remoteJob']);
Route::get('/remote/jobs/{id}/slides/{n}', [PptMasterController::class, 'remoteSlide']);
Route::post('/remote/jobs', [PptMasterController::class, 'remoteCreate']);
Route::post('/remote/jobs/{id}/confirm', [PptMasterController::class, 'remoteConfirm']);
Route::post('/remote/jobs/{id}/download', [PptMasterController::class, 'remoteDownload']);
