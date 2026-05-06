CTFd.plugin.run((_CTFd) => {
    const $ = _CTFd.lib.$
    const md = _CTFd.lib.markdown()
    
    $('a[href="#new-desc-preview"]').on('shown.bs.tab', function (event) {
        if (event.target.hash == '#new-desc-preview') {
            var editor_value = $('#new-desc-editor').val();
            $(event.target.hash).html(
                md.render(editor_value)
            );
        }
    });
    
    $(document).ready(function(){
        $('[data-toggle="tooltip"]').tooltip();
        
        // Bind radio button change events
        $('input[name="challenge_type"]').change(function() {
            const selectedType = $(this).val();

            
            if (typeof window.rebuildDropdown === 'function') {

                window.rebuildDropdown(selectedType);
            } else {
                console.error("rebuildDropdown function not found!");
            }
        });
        
        // Rebuild dropdown completely (avoids browser rendering issues with hide/show)
        window.rebuildDropdown = function(selectedType) {

            const dropdown = $('#dockerimage_select');
            
            // Clear the dropdown
            dropdown.empty();
            
            if (!window.allDockerOptions || window.allDockerOptions.length === 0) {
                dropdown.append($('<option value="" disabled>No Docker servers available</option>'));
                return;
            }
            
            // Filter and group items by server
            const serverGroups = {};
            let itemCount = 0;
            
            window.allDockerOptions.forEach(function(item) {
                // Include items that match the selected type
                if ((selectedType === 'single' && item.type === 'single') || 
                    (selectedType === 'multi' && item.type === 'multi')) {
                    
                    if (!serverGroups[item.server_name]) {
                        serverGroups[item.server_name] = [];
                    }
                    serverGroups[item.server_name].push(item);
                    itemCount++;
                }
            });
            
            if (itemCount === 0) {
                const typeLabel = selectedType === 'single' ? 'single-image' : 'multi-image';
                dropdown.append($(`<option value="" disabled>No ${typeLabel} challenges available</option>`));

                return;
            }
            
            // Build the dropdown structure
            Object.keys(serverGroups).sort().forEach(function(serverName) {
                const optgroup = $('<optgroup>').attr('label', serverName);
                
                serverGroups[serverName].forEach(function(item) {
                    const option = $('<option>').val(JSON.stringify(item));
                    
                    if (item.type === 'multi') {
                        const displayText = `[MULTI] ${item.project_name} (${item.image_count} images)`;
                        option.text(displayText)
                              .attr('data-type', 'multi')
                              .attr('data-services', item.services ? item.services.join(',') : '');

                    } else {
                        option.text(item.image_name)
                              .attr('data-type', 'single');

                    }
                    
                    optgroup.append(option);
                });
                
                dropdown.append(optgroup);
            });
            

        }
        
        // Handle docker selection change to populate primary service options
        $('#dockerimage_select').change(function() {
            const selectedOption = $(this).find('option:selected');
            const itemType = selectedOption.attr('data-type');
            
            if (itemType === 'multi') {
                // Multi-image selected - populate primary service options
                const services = selectedOption.attr('data-services');
                if (services) {
                    const serviceList = services.split(',');
                    const primaryServiceSelect = $('#primary_service');
                    primaryServiceSelect.empty();
                    primaryServiceSelect.append('<option value="">Auto-detect</option>');
                    
                    serviceList.forEach(function(service) {
                        primaryServiceSelect.append(
                            $('<option>').val(service).text(service)
                        );
                    });
                }
                
                // Auto-select multi-image challenge type (only if not already set)
                if (!$('#multi_image').prop('checked')) {
                    $('#multi_image').prop('checked', true).trigger('change');
                }
            } else if (itemType === 'single') {
                // Single image selected - auto-select single challenge type (only if not already set)
                if (!$('#single_image').prop('checked')) {
                    $('#single_image').prop('checked', true).trigger('change');
                }
            }
        });
        
        // Load Docker images and servers

        $.ajaxSetup({ timeout: 10000 }); // 10 second timeout
        $.getJSON("/api/v1/docker", function(result){

            
            if (result.success && result.data) {
                // Store all options globally for filtering
                window.allDockerOptions = result.data;

                
                // Get currently selected challenge type (default to single)
                let selectedType = $('input[name="challenge_type"]:checked').val();
                if (!selectedType) {

                    $('#single_image').prop('checked', true);
                    selectedType = 'single';
                }
                

                window.rebuildDropdown(selectedType);
                
            } else {
                // Handle error case
                console.error("Docker API returned error:", result);
                $("#dockerimage_select").empty().prop('disabled', true);
                $("label[for='DockerImage']").text('Docker Image - Error loading servers!');
                const errorMsg = (result.data && result.data[0] && result.data[0].name) || 'Error loading Docker servers';
                $("#dockerimage_select").append($("<option />").val('ERROR').text(errorMsg).attr('disabled', true));
            }
        }).fail(function(jqXHR, textStatus, errorThrown) {
            // Handle AJAX failure
            console.error("Docker API AJAX failed:", textStatus, errorThrown);
            $("#dockerimage_select").prop('disabled', true);
            $("label[for='DockerImage']").text('Docker Image - Connection Error!')
            $("#dockerimage_select").append($("<option />").val('ERROR').text('Failed to connect to Docker API').attr('disabled', true));
        });
    });
});